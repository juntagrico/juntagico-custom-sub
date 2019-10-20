from django.db import models
from juntagrico_custom_sub.entity.product import Product
from django.utils.translation import gettext as _


class CustomDelivery(models.Model):
    delivery_date = models.DateField(_('Lieferdatum'))
    def __str__(self):
        return u"%s" % (self.delivery_date)
    class Meta:
        verbose_name = _('Lieferung')
        verbose_name_plural = _('Lieferungen')



class CustomDeliveryProduct(models.Model):
    delivery = models.ForeignKey(CustomDelivery, verbose_name=_('Lieferung'),
                                 related_name='items',
                                 on_delete=models.CASCADE)
    product = models.ForeignKey(Product, verbose_name=_(
        'Produkt'), on_delete=models.CASCADE)
    name = models.CharField(_('Name'), max_length=100, default="")
    comment = models.CharField(_('Kommentar'), max_length=1000,
                               default="", blank=True)

    class Meta:
        verbose_name = _('Lieferobjekt')
        verbose_name_plural = _('Lieferobjekte')
        unique_together = ("product", "delivery")