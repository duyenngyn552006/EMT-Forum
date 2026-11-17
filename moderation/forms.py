from django import forms

from .models import AccountAppeal, Report, SensitiveKeyword


class ReportForm(forms.ModelForm):
    class Meta:
        model = Report
        fields = ["category", "reason"]
        labels = {"category": "Loại vi phạm", "reason": "Mô tả thêm"}
        widgets = {
            "category": forms.RadioSelect,
            "reason": forms.Textarea(attrs={"rows": 3, "placeholder": "Bắt buộc khi chọn \"Khác\""}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Khong chon san "Khac" (gia tri mac dinh cua model) - bat nguoi dung chu dong chon loai
        self.fields["category"].initial = None

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("category") == Report.Category.OTHER and not (cleaned.get("reason") or "").strip():
            self.add_error("reason", "Vui lòng mô tả lý do khi chọn \"Khác\".")
        return cleaned


class ReportResolveForm(forms.Form):
    # UC-8.16 - "Bo qua" = ket luan khong vi pham (UC-K02); 3 bien phap con lai ap dung khi co vi pham,
    # moi lan chi chon dung 1 bien phap, theo muc do vi pham tu nhe den nang.
    ACTION_CHOICES = [
        ("dismiss", "Bỏ qua báo cáo (không vi phạm)"),
        ("warn", "Cảnh báo người đăng (vi phạm nhẹ, giữ nội dung)"),
        ("hide", "Tạm ẩn nội dung (cần xác minh thêm, có thể khôi phục)"),
        ("delete", "Xóa nội dung (vi phạm rõ ràng, không khôi phục)"),
    ]
    action = forms.ChoiceField(choices=ACTION_CHOICES, label="Hướng xử lý")
    resolution_note = forms.CharField(widget=forms.Textarea, required=True, label="Lý do xử lý")

    def __init__(self, *args, content_missing=False, **kwargs):
        super().__init__(*args, **kwargs)
        if content_missing:
            # UC-8.16 ngoai le 3a: noi dung da bi nguoi dang xoa -> chi con dong bao cao
            self.fields["action"].choices = [self.ACTION_CHOICES[0]]


class AccountAppealForm(forms.ModelForm):
    class Meta:
        model = AccountAppeal
        fields = ["reason", "extra_info"]
        labels = {"reason": "Lý do kháng nghị", "extra_info": "Thông tin bổ sung (không bắt buộc)"}
        widgets = {"reason": forms.Textarea(attrs={"rows": 3}), "extra_info": forms.Textarea(attrs={"rows": 3})}


class AppealReviewForm(forms.Form):
    DECISION_CHOICES = [("accept", "Chấp nhận"), ("reject", "Từ chối")]
    decision = forms.ChoiceField(choices=DECISION_CHOICES)
    review_note = forms.CharField(widget=forms.Textarea, required=True, label="Ghi chú")


class ContentRejectForm(forms.Form):
    reason = forms.CharField(widget=forms.Textarea, label="Lý do từ chối", required=True)


class SensitiveKeywordForm(forms.ModelForm):
    class Meta:
        model = SensitiveKeyword
        fields = ["keyword", "is_active", "note"]
        labels = {"keyword": "Từ khóa", "is_active": "Đang áp dụng", "note": "Ghi chú"}
