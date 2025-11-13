import re

from django import forms


class MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class ReceptionForm(forms.Form):
    customer_name = forms.CharField(label="Nombre", max_length=100)
    customer_phone = forms.CharField(label="Telefono", max_length=30, required=False)
    customer_email = forms.EmailField(label="Email", required=False)
    brand = forms.CharField(label="Marca", max_length=50)
    model = forms.CharField(label="Modelo", max_length=50)
    serial = forms.CharField(label="Serie", max_length=100, required=False)
    notes = forms.CharField(label="Descripcion / Falla", widget=forms.Textarea, required=False)

    def _strip(self, key):
        value = (self.cleaned_data.get(key) or "").strip()
        return re.sub(r"\s+", " ", value)

    def clean_customer_name(self):
        return self._strip("customer_name")

    def clean_brand(self):
        return self._strip("brand")

    def clean_model(self):
        return self._strip("model")

    def clean_serial(self):
        return self._strip("serial")

    def clean_notes(self):
        return (self.cleaned_data.get("notes") or "").strip()

    def clean_customer_phone(self):
        phone = self._strip("customer_phone")
        if not phone:
            return ""
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 7:
            raise forms.ValidationError("Captura un telefono valido (7 digitos minimo).")
        return phone

    def clean_customer_email(self):
        email = (self.cleaned_data.get("customer_email") or "").strip()
        return email.lower()

    def clean(self):
        cleaned = super().clean()
        phone = cleaned.get("customer_phone", "")
        email = cleaned.get("customer_email", "")
        if not phone and not email:
            raise forms.ValidationError("Debes capturar al menos un telefono o un correo.")
        return cleaned


# === INTEGRASYS PATCH: ATTACHMENTS FORM ===
from core.models import Attachment, InventoryItem


class AttachmentForm(forms.ModelForm):
    class Meta:
        model = Attachment
        fields = [
            name
            for name in ("file", "caption")
            if any(getattr(f, "name", "") == name for f in Attachment._meta.fields)
        ]
        widgets = {
            "file": MultiFileInput(attrs={"multiple": True}),
        }


class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = ["sku", "name", "qty", "min_qty", "location"]
        widgets = {
            "sku": forms.TextInput(attrs={"autofocus": True}),
            "qty": forms.NumberInput(attrs={"min": 0}),
            "min_qty": forms.NumberInput(attrs={"min": 0}),
        }

    def clean_sku(self):
        sku = self.cleaned_data.get("sku", "")
        return sku.strip().upper()

    def clean_name(self):
        name = self.cleaned_data.get("name", "")
        return name.strip()

    def clean_location(self):
        location = self.cleaned_data.get("location", "")
        return location.strip()
