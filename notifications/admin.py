from django.contrib import admin

from .models import DeadlineCompletion, DeadlineReminderLog, Notification, NotificationPreference

admin.site.register(Notification)
admin.site.register(NotificationPreference)

admin.site.register(DeadlineReminderLog)
admin.site.register(DeadlineCompletion)
