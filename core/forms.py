from django import forms

class ReceptionForm(forms.Form):
    customer_name = forms.CharField(label="Nombre", max_length=100)
    customer_phone = forms.CharField(label="Teléfono", max_length=30, required=False)
    customer_email = forms.EmailField(label="Email", required=False)
    brand = forms.CharField(label="Marca", max_length=50)
    model = forms.CharField(label="Modelo", max_length=50)
    serial = forms.CharField(label="Serie", max_length=100, required=False)
    notes = forms.CharField(label="Descripción / Falla", widget=forms.Textarea, required=False)
