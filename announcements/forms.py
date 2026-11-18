import re

from django import forms
from django.forms import inlineformset_factory

from .models import Announcement, AnnouncementAttachment, EventRegistration


EVENT_MODE_SINGLE = "single"
EVENT_MODE_RANGE = "range"


class AnnouncementForm(forms.ModelForm):
    # Thoi gian dien ra su kien: 1 moc (chi event_datetime) hoac 1 khoang (event_datetime -> event_end_datetime).
    # Chi bat buoc khi kind=EVENT (xem clean())
    event_time_mode = forms.ChoiceField(
        label="Kiểu thời gian",
        choices=[(EVENT_MODE_SINGLE, "Một mốc thời gian"), (EVENT_MODE_RANGE, "Khoảng thời gian")],
        widget=forms.RadioSelect,
        initial=EVENT_MODE_SINGLE,
        required=False,
    )
    event_datetime = forms.SplitDateTimeField(
        label="Thời điểm diễn ra / bắt đầu",
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date", "aria-label": "Ngày bắt đầu"},
            time_attrs={"type": "time", "aria-label": "Giờ bắt đầu"},
        ),
        required=False,
    )
    event_end_datetime = forms.SplitDateTimeField(
        label="Kết thúc",
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date", "aria-label": "Ngày kết thúc"},
            time_attrs={"type": "time", "aria-label": "Giờ kết thúc"},
        ),
        required=False,
    )

    deadline_at = forms.SplitDateTimeField(
        label="Hạn chót đăng ký / nộp",
        widget=forms.SplitDateTimeWidget(
            date_attrs={"type": "date", "aria-label": "Ngày hết hạn"},
            time_attrs={"type": "time", "aria-label": "Giờ hết hạn"},
        ),
        required=False,
        help_text="Không bắt buộc. Hệ thống sẽ nhắc sinh viên trước hạn 2 ngày, 1 ngày và 3 giờ.",
    )

    class Meta:
        model = Announcement
        fields = ["kind", "title", "body", "featured_image", "event_datetime", "event_end_datetime", "deadline_at"]
        labels = {"kind": "Loại thông báo", "title": "Tiêu đề", "body": "Nội dung"}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from accounts.models import User

        # Sua bai da co khoang thoi gian -> mo san o che do "Khoang thoi gian"
        if self.instance.pk and self.instance.event_end_datetime:
            self.initial["event_time_mode"] = EVENT_MODE_RANGE

        # Giao vu Khoa, Giang vien va BCH Khoa deu dang duoc ca thong bao chinh thuc lan su kien.
        # Bai moi cua BCH mac dinh chon san "Su kien" (viec chinh cua BCH), cac vai tro khac mac dinh "Chinh thuc".
        if user and user.role == User.Role.BCH and not self.instance.pk:
            self.fields["kind"].initial = Announcement.Kind.EVENT

    def clean(self):
        cleaned_data = super().clean()
        is_event = cleaned_data.get("kind") == Announcement.Kind.EVENT
        start = cleaned_data.get("event_datetime")
        end = cleaned_data.get("event_end_datetime")
        deadline = cleaned_data.get("deadline_at")

        if is_event and not start:
            self.add_error("event_datetime", "Bắt buộc nhập thời gian diễn ra sự kiện đối với thông báo loại sự kiện.")

        if is_event and deadline and start and deadline >= start:
            self.add_error(
                "deadline_at",
                "Hạn chót đăng ký phải trước thời điểm bắt đầu diễn ra sự kiện.",
            )

        if is_event and cleaned_data.get("event_time_mode") == EVENT_MODE_RANGE:
            if start and not end:
                self.add_error("event_end_datetime", "Vui lòng nhập thời điểm kết thúc (hoặc chuyển sang “Một mốc thời gian”).")
            elif start and end and end <= start:
                self.add_error("event_end_datetime", "Thời điểm kết thúc phải sau thời điểm bắt đầu.")
        else:
            # Che do 1 moc (hoac khong phai su kien): bo gio ket thuc con sot lai tu lan chon truoc
            cleaned_data["event_end_datetime"] = None
        return cleaned_data


MAX_ATTACHMENTS = 10

# Mac dinh 1 o anh trong; them o bang nut "+ Them anh" (JS nhan ban formset.empty_form).
# O "Xoa" chi co o anh da luu (can_delete_extra=False), anh moi chua tai len thi khong can xoa.
AnnouncementAttachmentFormSet = inlineformset_factory(
    Announcement, AnnouncementAttachment, fields=["image", "caption"],
    extra=1, can_delete=True, can_delete_extra=False, max_num=MAX_ATTACHMENTS, validate_max=True,
    labels={"image": "Ảnh", "caption": "Chú thích"},
    widgets={
        "image": forms.FileInput(attrs={"accept": "image/*"}),
        "caption": forms.TextInput(attrs={"placeholder": "Chú thích (không bắt buộc)"}),
    },
)


class EventRegistrationForm(forms.ModelForm):
    class Meta:
        model = EventRegistration
        fields = ["full_name", "student_id", "class_name", "email", "phone", "note"]
        labels = {
            "full_name": "Họ và tên",
            "student_id": "Mã số sinh viên / Cán bộ",
            "class_name": "Lớp",
            "email": "Email liên hệ",
            "phone": "Số điện thoại",
            "note": "Ghi chú / Câu hỏi cho BTC",
        }
        widgets = {
            "full_name": forms.TextInput(attrs={"placeholder": "VD: Nguyễn Văn A", "required": True}),
            "student_id": forms.TextInput(attrs={"placeholder": "VD: 221121514101", "required": True}),
            "class_name": forms.TextInput(attrs={"placeholder": "VD: 48K22.1"}),
            "email": forms.EmailInput(attrs={"placeholder": "VD: email@due.udn.vn", "required": True}),
            "phone": forms.TextInput(attrs={"placeholder": "VD: 0905123456", "type": "tel", "required": True}),
            "note": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": "Câu hỏi, yêu cầu hỗ trợ hoặc mong muốn khi tham gia (không bắt buộc)...",
                }
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        self.fields["full_name"].required = True
        self.fields["student_id"].required = True
        self.fields["email"].required = True
        self.fields["phone"].required = True
        self.fields["note"].required = False

        if user and getattr(user, "has_class_name_field", False):
            self.fields["class_name"].required = True
            self.fields["class_name"].widget.attrs["required"] = True
        else:
            self.fields["class_name"].required = False

        if user and not self.is_bound:
            self.fields["full_name"].initial = user.get_full_name()
            self.fields["student_id"].initial = user.student_id
            self.fields["class_name"].initial = user.class_name
            self.fields["email"].initial = user.email
            self.fields["phone"].initial = user.phone

    def clean_full_name(self):
        value = (self.cleaned_data.get("full_name") or "").strip()
        if not value:
            raise forms.ValidationError("Vui lòng nhập họ và tên.")
        return value

    def clean_student_id(self):
        value = (self.cleaned_data.get("student_id") or "").strip()
        if not value:
            raise forms.ValidationError("Vui lòng nhập mã số sinh viên / cán bộ.")
        return value

    def clean_class_name(self):
        value = (self.cleaned_data.get("class_name") or "").strip()
        if self.fields["class_name"].required and not value:
            raise forms.ValidationError("Vui lòng nhập lớp của bạn.")
        return value

    def clean_phone(self):
        value = (self.cleaned_data.get("phone") or "").strip()
        digits = re.sub(r"\D", "", value)
        if len(digits) < 9 or len(digits) > 15:
            raise forms.ValidationError("Số điện thoại không hợp lệ (cần từ 9 đến 15 chữ số).")
        return value

    def clean_note(self):
        return (self.cleaned_data.get("note") or "").strip()
