from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "docuquery",
    broker=settings.redis_url,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_ignore_result=True,
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    # Solo pool is used on Windows dev; containers use prefork in production.
    worker_pool="solo" if settings.debug else "prefork",
    broker_connection_retry_on_startup=True,
)
