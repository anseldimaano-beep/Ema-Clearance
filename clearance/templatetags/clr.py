from django import template
from ..i18n import T

register = template.Library()


@register.simple_tag(takes_context=True)
def tr(context, text):
    return T(context.get("lang", "en"), text)
