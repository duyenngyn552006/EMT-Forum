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
    ACTION_CHOICES = [
        ("dismiss", "Bỏ qua báo cáo"),
        ("hide", "Ẩn nội dung"),
        ("delete", "Xóa nội dung"),
    ]
    action = forms.ChoiceField(choices=ACTION_CHOICES, label="Hướng xử lý")
    resolution_note = forms.CharField(widget=forms.Textarea, required=True, label="Ghi chú xử lý")


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
