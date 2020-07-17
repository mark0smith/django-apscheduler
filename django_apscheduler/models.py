from datetime import timedelta

from django.db import models, connection
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _

import time
import logging

from django_apscheduler import util

logger = logging.getLogger("django_apscheduler")


# TODO: Remove this workaround?
# The canonical approach seems to be to close connections instead if we know that they will be idle for a long
# period of time - see: https://code.djangoproject.com/ticket/21597#comment:29
class DjangoJobManager(models.Manager):
    """
    This manager pings database each request after 30s IDLE to prevent MysqlGoneAway error
    """

    _last_ping = 0
    _ping_interval = 30

    def get_queryset(self):
        self.__ping()
        return super().get_queryset()

    def __ping(self):
        if time.time() - self._last_ping < self._ping_interval:
            return

        try:
            with connection.cursor() as c:
                c.execute("SELECT 1")
        # TODO: Make this except clause more specific
        except Exception:
            self.__reconnect()

        self._last_ping = time.time()

    def __reconnect(self):
        logger.warning("Mysql closed the connection. Perform reconnect...")

        if connection.connection:
            connection.connection.close()
            connection.connection = None
        else:
            logger.warning("Connection was already closed.")


class DjangoJob(models.Model):
    id = models.CharField(
        max_length=255, primary_key=True, help_text=_("Unique id for this job.")
    )

    next_run_time = models.DateTimeField(
        db_index=True,
        blank=True,
        null=True,
        help_text=_(
            "Date and time at which this job is scheduled to be executed next."
        ),
    )

    # TODO: Consider using PickleField instead.
    job_state = models.BinaryField()

    objects = DjangoJobManager()

    def __str__(self):
        status = (
            f"next run at: {util.get_local_dt_format(self.next_run_time)}"
            if self.next_run_time
            else "paused"
        )
        return f"{self.id} ({status})"

    class Meta:
        ordering = ("next_run_time",)


class DjangoJobExecutionManager(models.Manager):
    def delete_old_job_executions(self, max_age: int):
        """
        Delete old job executions from the database.

        :param max_age: The maximum age (in seconds). Executions that are older
        than this will be deleted.
        """
        self.filter(run_time__lte=timezone.now() - timedelta(seconds=max_age),).delete()


class DjangoJobExecution(models.Model):
    SUCCESS = "Executed"
    SENT = "Started execution"
    ERROR = "Error!"

    STATUS_CHOICES = [(x, x) for x in [SENT, ERROR, SUCCESS,]]

    job = models.ForeignKey(
        DjangoJob,
        on_delete=models.CASCADE,
        help_text=_("The job that this execution relates to."),
    )
    status = models.CharField(
        max_length=50,
        # TODO: Replace this with enumeration types when we drop support for Django 2.2
        # See: https://docs.djangoproject.com/en/dev/ref/models/fields/#field-choices-enum-types
        choices=STATUS_CHOICES,
        help_text=_("The current status of this job execution."),
    )
    run_time = models.DateTimeField(
        db_index=True, help_text=_("Date and time at which this job was executed."),
    )

    duration = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=None,
        null=True,
        help_text=_("Total run time of this job (in seconds)."),
    )

    started = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=None,
        null=True,
        help_text=_("Timestamp at which this job was started."),
    )

    finished = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        default=None,
        null=True,
        help_text=_("Timestamp at which this job was finished."),
    )

    exception = models.CharField(
        max_length=1000,
        null=True,
        help_text=_(
            "Details of exception that occurred during job execution (if any)."
        ),
    )

    traceback = models.TextField(
        null=True,
        help_text=_(
            "Traceback of exception that occurred during job execution (if any)."
        ),
    )

    objects = DjangoJobExecutionManager()

    def __unicode__(self):
        return f"Execution id={self.id}, status={self.status}, job={self.job}"

    class Meta:
        ordering = ("-run_time",)
