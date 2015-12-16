# -*- coding: utf-8 -*-
from django.utils import timezone
import django.contrib.postgres.fields as pgfields
from django.conf import settings
from django.contrib.gis.db import models
from django.utils.translation import ugettext_lazy as _
from django.core.exceptions import ValidationError
from psycopg2.extras import DateTimeTZRange
from django.template.loader import render_to_string

from .base import ModifiableModel
from .utils import get_dt, save_dt, is_valid_time_slot, humanize_duration, send_respa_mail


class ReservationQuerySet(models.QuerySet):
    def active(self):
        return self.filter(end__gte=timezone.now())


class Reservation(ModifiableModel):
    resource = models.ForeignKey('Resource', verbose_name=_('Resource'), db_index=True, related_name='reservations')
    begin = models.DateTimeField(verbose_name=_('Begin time'))
    end = models.DateTimeField(verbose_name=_('End time'))
    duration = pgfields.DateTimeRangeField(verbose_name=_('Length of reservation'), null=True,
                                           blank=True, db_index=True)
    comments = models.TextField(null=True, blank=True, verbose_name=_('Comments'))
    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name=_('User'), null=True,
                             blank=True, db_index=True)

    def _save_dt(self, attr, dt):
        """
        Any DateTime object is converted to UTC time zone aware DateTime
        before save

        If there is no time zone on the object, resource's time zone will
        be assumed through its unit's time zone
        """
        save_dt(self, attr, dt, self.resource.unit.time_zone)

    def _get_dt(self, attr, tz):
        return get_dt(self, attr, tz)

    @property
    def begin_tz(self):
        return self.begin

    @begin_tz.setter
    def begin_tz(self, dt):
        self._save_dt('begin', dt)

    def get_begin_tz(self, tz):
        return self._get_dt("begin", tz)

    @property
    def end_tz(self):
        return self.end

    @end_tz.setter
    def end_tz(self, dt):
        """
        Any DateTime object is converted to UTC time zone aware DateTime
        before save

        If there is no time zone on the object, resource's time zone will
        be assumed through its unit's time zone
        """
        self._save_dt('end', dt)

    def get_end_tz(self, tz):
        return self._get_dt("end", tz)

    def is_active(self):
        return self.end >= timezone.now()

    class Meta:
        verbose_name = _("reservation")
        verbose_name_plural = _("reservations")

    def __str__(self):
        return "%s -> %s: %s" % (self.begin, self.end, self.resource)

    def clean(self, **kwargs):
        """
        Check restrictions that are common to all reservations.

        If this reservation isn't yet saved and it will modify an existing reservation,
        the original reservation need to be provided in kwargs as 'original_reservation', so
        that it can be excluded when checking if the resource is available.
        """
        if self.end <= self.begin:
            raise ValidationError(_("You must end the reservation after it has begun"))

        # Check that begin and end times are on valid time slots.
        opening_hours = self.resource.get_opening_hours(self.begin.date(), self.end.date())
        for dt in (self.begin, self.end):
            days = opening_hours.get(dt.date(), [])
            day = next((day for day in days if day['opens'] is not None and day['opens'] <= dt <= day['closes']), None)
            if day and not is_valid_time_slot(dt, self.resource.min_period, day['opens']):
                raise ValidationError(_("Begin and end time must match time slots"))

        original_reservation = self if self.pk else kwargs.get('original_reservation', None)
        if not self.resource.is_available(self.begin, self.end, original_reservation):
            raise ValidationError(_("The resource is already reserved for some of the period"))

        if (self.end - self.begin) < self.resource.min_period:
            raise ValidationError(_("The minimum reservation length is %(min_period)s") %
                                  {'min_period': humanize_duration(self.min_period)})

    def send_created_by_admin_mail(self):
        mail_content = render_to_string(
            'mail/reservation_created_by_admin.txt', {'reservation': self}
        )
        send_respa_mail(self.user, _('Reservation created'), mail_content)

    def send_updated_by_admin_mail_if_changed(self, old_reservation):
        for field in ('resource', 'begin', 'end'):
            if getattr(old_reservation, field) != getattr(self, field):
                mail_content = render_to_string(
                    'mail/reservation_updated_by_admin.txt',
                    {'reservation': self, 'old_reservation': old_reservation}
                )
                send_respa_mail(self.user, _('Reservation updated'), mail_content)
                break

    def send_deleted_by_admin_mail(self):
        mail_content = render_to_string(
            'mail/reservation_deleted_by_admin.txt', {'reservation': self}
        )
        send_respa_mail(self.user, _('Reservation deleted'), mail_content)

    def save(self, *args, **kwargs):
        self.duration = DateTimeTZRange(self.begin, self.end)
        return super().save(*args, **kwargs)

    objects = ReservationQuerySet.as_manager()
