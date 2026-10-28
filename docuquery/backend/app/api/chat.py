"""Streaming chat with citations + conversation history."""

import json
import logging
import time
import uuid as uuidlib

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import ChatRequest, Citation, ConversationOut, MessageOut
from app.config import get_settings
from app.db.models import Conversation, Message, QueryLog
from app.db.session import get_db
from app.services.llm import SYSTEM_PROMPT, llm_client
from app.services.retrieval import Source, retrieve

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])

HISTORY_LIMIT = 6  # previous messages sent to the model


def _build_context(sources: list[Source]) -> str:
    parts = []
    for i, s in enumerate(sources, start=1):
        parts.append(f"[{i}] ({s.document_name}, page {s.page})\n{s.content}")
    return "\n\n".join(parts)


async def _history(db: AsyncSession, conversation_id: uuidlib.UUID) -> list[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(desc(Message.created_at))
        .limit(HISTORY_LIMIT)
    )
    return list(reversed(result.scalars().all()))


@router.post("/chat")
async def chat(body: ChatRequest, db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    started = time.perf_counter()

    # Conversation (create lazily; title = first question snippet)
    if body.conversation_id:
        conversation = await db.get(Conversation, body.conversation_id)
        if conversation is None:
            raise HTTPException(404, "Conversation not found.")
    else:
        conversation = Conversation(title=body.question[:80])
        db.add(conversation)
        await db.flush()

    # Retrieval
    sources, methods = await retrieve(db, body.question, top_k=body.top_k)
    retrieval_ms = round((time.perf_counter() - started) * 1000)

    # Persist user message
    user_message = Message(conversation_id=conversation.id, role="user", content=body.question)
    db.add(user_message)
    await db.commit()

    history = await _history(db, conversation.id)
    context = _build_context(sources) if sources else "NO EXCERPTS AVAILABLE."

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history[:-1]:  # exclude the just-asked question; it goes last with context
        messages.append({"role": msg.role, "content": msg.content[:2000]})
    messages.append(
        {
            "role": "user",
            "content": f"CONTEXT:\n{context}\n\nQUESTION: {body.question}\n\n"
            "Answer using only the context. Cite excerpts as [n].",
        }
    )

    citations = [
        Citation(
            n=i,
            document_id=s.document_id,
            document_name=s.document_name,
            page=s.page,
            snippet=s.snippet,
        ).model_dump(mode="json")
        for i, s in enumerate(sources, start=1)
    ]

    conversation_id = conversation.id

    async def event_stream():
        client = llm_client()
        llm_started = time.perf_counter()
        answer_parts: list[str] = []
        prompt_tokens = completion_tokens = 0

        try:
            yield f"data: {json.dumps({'type': 'meta', 'conversation_id': str(conversation_id), 'retrieval_ms': retrieval_ms, 'methods': methods})}\n\n"
            yield f"data: {json.dumps({'type': 'citations', 'citations': citations})}\n\n"

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
                    yield f"data: {json.dumps({'type': 'token', 'value': delta})}\n\n"

            answer = "".join(answer_parts).strip()
            llm_ms = round((time.perf_counter() - llm_started) * 1000)
            total_ms = round((time.perf_counter() - started) * 1000)

            if not completion_tokens:
                completion_tokens = max(1, round(len(answer) / 4))
            if not prompt_tokens:
                prompt_tokens = max(1, round((len(context) + len(body.question)) / 4))

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
                    question=body.question,
                    model=settings.llm_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    retrieval_ms=retrieval_ms,
                    llm_ms=llm_ms,
                    total_ms=total_ms,
                    retrieved_chunk_ids=[str(s.chunk_id) for s in sources],
                )
            )
            await db.commit()

            yield f"data: {json.dumps({'type': 'done', 'message_id': str(assistant_message.id), 'prompt_tokens': prompt_tokens, 'completion_tokens': completion_tokens, 'latency_ms': total_ms})}\n\n"
        except Exception as exc:  # noqa: BLE001 - stream errors must reach the client
            logger.exception("Chat streaming failed")
            await db.rollback()
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc)[:500]})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(
            Conversation,
            func.count(Message.id).label("message_count"),
        )
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .group_by(Conversation.id)
        .order_by(desc(Conversation.updated_at))
    )
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


@router.get("/conversations/{conversation_id}", response_model=list[MessageOut])
async def get_conversation(conversation_id: uuidlib.UUID, db: AsyncSession = Depends(get_db)):
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    return list(result.scalars().all())


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: uuidlib.UUID, db: AsyncSession = Depends(get_db)):
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "Conversation not found.")
    await db.delete(conversation)
    await db.commit()
