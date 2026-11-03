from django import forms
from django.utils import timezone

MAX_POLL_OPTIONS = 8


class PollForm(forms.Form):
    # required=False o ca 2 field: form nay nhung trong form dang bai (tuy chon, co the
    # bo trong het de khong tao poll) - de required=True mac dinh se sinh thuoc tinh HTML
    # "required", trinh duyet chan submit ca bai dang dang du poll dang de trong.
    # Viec bat buoc "da nhap cau hoi thi phai co >=2 lua chon" van do clean_options() lo,
    # chi chay khi view xac dinh nguoi dung THUC SU muon tao poll (xem forum/groups views).
    question = forms.CharField(label="Câu hỏi bình chọn", max_length=255, required=False)
    options = forms.CharField(
        label=f"Các lựa chọn (mỗi dòng 1 lựa chọn, từ 2 đến {MAX_POLL_OPTIONS})",
        widget=forms.Textarea(attrs={"rows": 5}), required=False,
    )
    closes_at = forms.DateTimeField(
        label="Hạn chốt (không bắt buộc, để trống = không giới hạn)", required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
    )

    def clean_options(self):
        lines = [line.strip() for line in self.cleaned_data["options"].splitlines() if line.strip()]
        # Loai trung (khong phan biet hoa/thuong) nhung giu thu tu nhap
        seen, options = set(), []
        for line in lines:
            if line.lower() not in seen:
                seen.add(line.lower())
                options.append(line)
        if len(options) < 2:
            raise forms.ValidationError("Cần ít nhất 2 lựa chọn khác nhau.")
        if len(options) > MAX_POLL_OPTIONS:
            raise forms.ValidationError(f"Tối đa {MAX_POLL_OPTIONS} lựa chọn.")
        if any(len(o) > 200 for o in options):
            raise forms.ValidationError("Mỗi lựa chọn tối đa 200 ký tự.")
        return options

    def clean_closes_at(self):
        closes_at = self.cleaned_data.get("closes_at")
        if closes_at and closes_at <= timezone.now():
            raise forms.ValidationError("Hạn chốt phải ở tương lai.")
        return closes_at
