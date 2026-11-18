from django.contrib import admin

from .models import Announcement, AnnouncementAttachment, AnnouncementEditHistory, EventRegistration


class AnnouncementAttachmentInline(admin.TabularInline):
    model = AnnouncementAttachment
    extra = 1


class AnnouncementEditHistoryInline(admin.TabularInline):
    model = AnnouncementEditHistory
    extra = 0
    readonly_fields = ("editor", "title", "body", "edited_at")
    can_delete = False


class EventRegistrationInline(admin.TabularInline):
    model = EventRegistration
    extra = 0
    readonly_fields = ("user", "full_name", "student_id", "class_name", "email", "phone", "note", "registered_at")
    can_delete = False


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "kind", "status", "is_pinned", "edit_count", "created_at")
    list_filter = ("kind", "status", "is_pinned")
    search_fields = ("title", "body", "author__email")
    inlines = [AnnouncementAttachmentInline, AnnouncementEditHistoryInline, EventRegistrationInline]


@admin.register(EventRegistration)
class EventRegistrationAdmin(admin.ModelAdmin):
    list_display = ("full_name", "student_id", "class_name", "email", "phone", "announcement", "registered_at")
    list_filter = ("announcement", "registered_at")
    search_fields = ("full_name", "student_id", "class_name", "email", "phone", "announcement__title")
    readonly_fields = ("registered_at",)
