from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_list, name="list"),
    path("<int:pk>/read/", views.mark_read, name="mark_read"),
    path("<int:pk>/click/", views.click_notification, name="click"),
    path("read-all/", views.mark_all_read, name="mark_all_read"),
    path("preferences/", views.preferences, name="preferences"),
    path("deadlines/", views.deadline_list, name="deadlines"),
    path("deadlines/<int:announcement_pk>/complete/", views.complete_deadline, name="complete_deadline"),
    path("deadlines/<int:announcement_pk>/uncomplete/", views.uncomplete_deadline, name="uncomplete_deadline"),
]
