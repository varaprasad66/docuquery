"""Streaming chat with citations + conversation history.

Security: retrieval is tenant-scoped (a user's questions only ever draw on their own
+ public documents). Errors streamed to clients are sanitized; details stay in logs.
"""

import json
import logging
import time
import uuid as uuidlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import (
    get_current_user,
    rate_limit_chat,
    require_api_key_scope,
    require_jwt_session,
)
from app.api.schemas import (
    ChatRequest,
    Citation,
    ConversationOut,
    FeedbackRequest,
    MessageOut,
)
from app.config import get_settings
from app.db.models import Conversation, Message, QueryLog, User
from app.db.session import get_db
from app.editions import require_professional
from app.services import query_rewriter, semantic_cache
from app.services.costs import calculate_cost_usd
from app.services.llm import llm_client
from app.services.prompting import build_prompt
from app.services.grounding import REFUSAL, GroundingResult, verify_answer
from app.services.retrieval import Source, retrieve

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


SANITIZED_STREAM_ERROR = "The assistant is temporarily unavailable. Please try again."


def _build_context(sources: list[Source]) -> str:
    """Excerpts are untrusted document data: they are wrapped in explicit
    delimiters so prompt-injection text inside documents stays data. Each
    excerpt carries its neighbor-expanded context (if any)."""
    parts = []
    for i, s in enumerate(sources, start=1):
        parts.append(f"[{i}] ({s.document_name}, {s.source_label})\n{s.context_text}")
    body = "\n\n".join(parts)
    return (
        "<untrusted_document_context>\n"
        f"{body}\n"
        "</untrusted_document_context>"
    )


async def _history(db: AsyncSession, conversation_id: uuidlib.UUID) -> list[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(desc(Message.created_at))
        .limit(get_settings().history_limit + 1)
    )
    return list(reversed(result.scalars().all()))


async def _get_owned_conversation(
    db: AsyncSession,
    conversation_id: uuidlib.UUID,
    user: User | None,
) -> Conversation:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    if user is not None:
        # Authenticated users may only touch their own conversations. A guest's
        # conversation (owner NULL) is never visible to an authenticated user.
        if conversation.owner_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    else:
        if conversation.owner_id is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    return conversation


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@router.post("/chat", dependencies=[Depends(rate_limit_chat), Depends(require_api_key_scope("chat"))])
async def chat(
    body: ChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    settings = get_settings()
    started = time.perf_counter()
    owner_id = user.id if user else None

    # Daily question quota (professional edition; 0 = unlimited).
    if user and settings.max_daily_questions > 0:
        midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        await db.execute(select(User.id).where(User.id == user.id).with_for_update())
        count_res = await db.execute(
            select(func.count(Message.id)).join(Conversation).where(
                Conversation.owner_id == user.id, Message.created_at >= midnight, Message.role == "user"
            )
        )
        if int(count_res.scalar_one() or 0) >= settings.max_daily_questions:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Daily question quota reached ({settings.max_daily_questions}/day).",
            )

    # Conversation (create lazily; title = first question snippet)
    if body.conversation_id:
        conversation = await _get_owned_conversation(db, body.conversation_id, user)
    else:
        conversation = Conversation(
            title=body.question[:80],
            owner_id=owner_id,
        )
        db.add(conversation)
        await db.flush()

    conversation_id = conversation.id

    # Persist user message
    user_message = Message(conversation_id=conversation_id, role="user", content=body.question)
    db.add(user_message)
    await db.commit()

    async def event_stream():
        prompt_tokens = completion_tokens = 0
        llm_ms = 0

        try:
            # --- Follow-up rewriter: make the latest question self-contained ---
            # With no history this is a no-op returning the original question.
            history = await _history(db, conversation_id)
            search_query = await query_rewriter.rewrite_question(body.question, history)

            # --- Semantic cache (professional): answer repeated questions cheaply ---
            if settings.cache_enabled:
                try:
                    from app.services.embeddings import embed_texts

                    qvec = (await embed_texts([search_query], task="query"))[0]
                    qvec_literal = "[" + ",".join(f"{x:.7g}" for x in qvec) + "]"
                    hit = await semantic_cache.lookup(
                        db, owner_id, qvec_literal, search_query,
                        document_ids=[str(d) for d in body.document_ids] if body.document_ids else None,
                    )
                    if hit:
                        assistant = Message(conversation_id=conversation_id, role="assistant", content=hit.answer, citations=hit.citations)
                        db.add(assistant)
                        await db.flush()
                        cached_message_id = str(assistant.id)
                        retrieval_ms = round((time.perf_counter() - started) * 1000)
                        total_ms = retrieval_ms
                        db.add(
                            QueryLog(
                                owner_id=owner_id,
                                question=body.question,
                                model=f"{settings.llm_model} (cached)",
                                prompt_tokens=0,
                                completion_tokens=hit.completion_tokens,
                                cost_usd=0.0,
                                cached=True,
                                retrieval_ms=retrieval_ms,
                                llm_ms=0,
                                total_ms=total_ms,
                            )
                        )
                        assistant.prompt_tokens = 0
                        assistant.completion_tokens = hit.completion_tokens
                        assistant.latency_ms = total_ms
                        await db.commit()
                        yield _sse({
                            "type": "meta",
                            "conversation_id": str(conversation_id),
                            "retrieval_ms": retrieval_ms,
                            "methods": hit.methods or ["cache"],
                            "cached": True,
                        })
                        yield _sse({"type": "citations", "citations": hit.citations})
                        for piece in [hit.answer[i:i + 24] for i in range(0, len(hit.answer), 24)]:
                            yield _sse({"type": "token", "value": piece})
                        yield _sse({
                            "type": "done",
                            "prompt_tokens": 0,
                            "completion_tokens": hit.completion_tokens,
                            "latency_ms": total_ms,
                            "message_id": cached_message_id,
                            "cached": True,
                        })
                        return
                except Exception:
                    logger.warning("Cache path failed; answering normally", exc_info=True)
                    await db.rollback()

            # --- Retrieval (tenant-scoped, uses the standalone query) ---
            sources, methods = await retrieve(
                db,
                search_query,
                top_k=body.top_k,
                owner_id=owner_id,
                document_ids=body.document_ids,
            )
            retrieval_ms = round((time.perf_counter() - started) * 1000)

            yield _sse({
                "type": "meta",
                "conversation_id": str(conversation_id),
                "retrieval_ms": retrieval_ms,
                "methods": methods,
                "cached": False,
            })

            # --- Relevance floor: below it, refuse rather than guess ---
            # Applies to the reranker score when RERANKER_ENABLED, else to the
            # best primitive score (vector cosine / keyword rank) kept through
            # fusion. 0.0 disables the check entirely.
            low_relevance = False
            if sources and settings.min_relevance > 0:
                best = max(
                    (s.score if settings.reranker_enabled else s.relevance) for s in sources
                )
                if best < settings.min_relevance:
                    logger.info(
                        "reason=relevance_floor best_score=%.3f floor=%.3f chunks=%d",
                        best, settings.min_relevance, len(sources),
                    )
                    low_relevance = True
            if low_relevance:
                # Below the floor: skip generation entirely, refuse explicitly.
                sources = []

            sources, context, messages, prompt_chars = build_prompt(body.question, search_query, history[:-1], sources, _build_context)
            citations = [
                Citation(
                    n=i,
                    document_id=s.document_id,
                    document_name=s.document_name,
                    page=s.page,
                    location=s.location,
                    snippet=s.snippet,
                ).model_dump(mode="json")
                for i, s in enumerate(sources, start=1)
            ]

            # --- LLM streaming ---
            client = llm_client()
            llm_started = time.perf_counter()
            answer_parts: list[str] = []

            if sources:
                stream = await client.chat.completions.create(
                    model=settings.llm_model,
                    messages=messages,
                    temperature=settings.llm_temperature,
                    max_tokens=settings.llm_max_tokens,
                    stream=True,
                    stream_options={"include_usage": True},
                )
                async for chunk in stream:
                    if chunk.usage:
                        prompt_tokens = chunk.usage.prompt_tokens or prompt_tokens
                        completion_tokens = chunk.usage.completion_tokens or completion_tokens
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content
                    if delta:
                        answer_parts.append(delta)

            draft_answer = "".join(answer_parts).strip()
            if low_relevance:
                result = GroundingResult(REFUSAL, [], 0, 0, "low_relevance", False)
            else:
                result = await verify_answer(
                    client, body.question, draft_answer, sources, citations,
                )
            answer, citations, verifier_prompt, verifier_completion, refusal_reason, unverified = result
            logger.info("reason=%s prompt_chars=%d chunks=%s", refusal_reason, prompt_chars, [str(source.chunk_id) for source in sources])
            prompt_tokens += verifier_prompt
            completion_tokens += verifier_completion
            yield _sse({"type": "citations", "citations": citations})
            for offset in range(0, len(answer), 32):
                yield _sse({"type": "token", "value": answer[offset:offset + 32]})
            llm_ms = round((time.perf_counter() - llm_started) * 1000)
            total_ms = round((time.perf_counter() - started) * 1000)

            if not completion_tokens:
                completion_tokens = max(1, round(len(answer) / 4))
            if not prompt_tokens:
                prompt_tokens = max(1, round((len(context) + len(body.question)) / 4))
            cost_usd = calculate_cost_usd(prompt_tokens, completion_tokens)

            assistant_message = Message(
                conversation_id=conversation_id,
                role="assistant",
                content=answer,
                citations=citations,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=total_ms,
            )
            db.add(assistant_message)
            db.add(
                QueryLog(
                    owner_id=owner_id,
                    question=body.question,
                    model=settings.llm_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=cost_usd,
                    cached=False,
                    retrieval_ms=retrieval_ms,
                    llm_ms=llm_ms,
                    total_ms=total_ms,
                    retrieved_chunk_ids=[str(s.chunk_id) for s in sources],
                    refusal_reason=refusal_reason,
                    prompt_chars=prompt_chars,
                    draft_answer=(draft_answer[:4000] or None) if (settings.log_draft_answers if settings.log_draft_answers is not None else not settings.is_production) else None,
                )
            )
            await db.commit()

            saved_message_id = str(assistant_message.id)
            # --- Cache the fresh answer (professional) ---
            if settings.cache_enabled and sources and citations and refusal_reason == "ok" and not unverified:
                try:
                    from app.services.embeddings import embed_texts

                    qvec = (await embed_texts([search_query], task="query"))[0]
                    qvec_literal = "[" + ",".join(f"{x:.7g}" for x in qvec) + "]"
                    await semantic_cache.store(
                        db, owner_id, search_query, qvec_literal, answer, citations,
                        methods, prompt_tokens, completion_tokens,
                        document_ids=[str(d) for d in body.document_ids] if body.document_ids else None,
                    )
                except Exception:
                    logger.warning("Cache store failed", exc_info=True)

            yield _sse({
                "type": "done",
                "message_id": saved_message_id,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "latency_ms": total_ms,
                "cached": False,
                "refusal_reason": refusal_reason,
                "unverified": unverified,
            })
        except Exception:  # noqa: BLE001 - stream errors must reach the client
            logger.exception("Chat streaming failed")
            await db.rollback()
            # Sanitized error: internals never reach the client.
            yield _sse({"type": "error", "error": SANITIZED_STREAM_ERROR, "detail_code": "llm_stream_failed"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations", response_model=list[ConversationOut], dependencies=[Depends(require_api_key_scope("read"))])
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    base = (
        select(
            Conversation,
            func.count(Message.id).label("message_count"),
        )
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .group_by(Conversation.id)
        .order_by(desc(Conversation.updated_at))
    )
    if user is not None:
        query = base.where(Conversation.owner_id == user.id)
    else:
        query = base.where(Conversation.owner_id.is_(None))

    result = await db.execute(query)
    conversations = []
    for conversation, message_count in result.all():
        conversations.append(
            ConversationOut(
                id=conversation.id,
                title=conversation.title,
                created_at=conversation.created_at,
                message_count=message_count,
            )
        )
    return conversations


@router.get("/conversations/{conversation_id}", response_model=list[MessageOut], dependencies=[Depends(require_api_key_scope("read"))])
async def get_conversation(
    conversation_id: uuidlib.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    await _get_owned_conversation(db, conversation_id, user)
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return list(result.scalars().all())


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_jwt_session)])
async def delete_conversation(
    conversation_id: uuidlib.UUID,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    conversation = await _get_owned_conversation(db, conversation_id, user)
    await db.delete(conversation)
    await db.commit()


@router.post(
    "/messages/{message_id}/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_professional), Depends(require_jwt_session)],
)
async def leave_feedback(
    message_id: uuidlib.UUID,
    body: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(get_current_user),
):
    """Rate an assistant answer. Only the conversation owner (or the guest who asked)
    can rate; only assistant messages accept feedback."""
    message = await db.get(Message, message_id)
    if message is None or message.role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found.")
    await _get_owned_conversation(db, message.conversation_id, user)

    message.feedback_rating = body.rating
    message.feedback_comment = body.comment
    await db.commit()
    return None
