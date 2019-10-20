from django.contrib.auth.decorators import login_required

from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render,get_object_or_404,redirect
from django.http import HttpResponseRedirect
from juntagrico_custom_sub.models import *
from juntagrico.models import *
from juntagrico.views import get_menu_dict
from juntagrico.util.management_list import get_changedate
from juntagrico.util import return_to_previous_location
from juntagrico.dao.subscriptiondao import SubscriptionDao
from juntagrico.util.views_admin import subscription_management_list
from juntagrico_custom_sub.util.sub_content import new_content_valid,calculate_future_size,calculate_current_size
import logging

logger = logging.getLogger(__name__)

def subscription_content_edit(request,subscription_id=None):
    returnValues = dict()
    member = request.user.member
    if subscription_id is None:
        subscription = member.subscription
    else:
        subscription = get_object_or_404(Subscription, id=subscription_id)
    try:
        subContent = SubscriptionContent.objects.get(subscription=subscription)
    except SubscriptionContent.DoesNotExist:
        subContent = SubscriptionContent.objects.create(subscription=subscription)
        subContent.save()
        for type in subscription.future_types.all():
            for mandatoryProd in SubscriptionSizeMandatoryProducts.objects.filter(subscription_size=type.size):
                subItem = SubscriptionContentFutureItem.objects.create(subscription_content=subContent,product=mandatoryProd.product,amount=mandatoryProd.amount)
                subItem.save()
    products = Product.objects.all().order_by('user_editable')
    if "saveContent" in request.POST:
        valid,error = new_content_valid(subscription,request,products)
        if valid:
            for product in products:
                subItem,p  = SubscriptionContentFutureItem.objects.get_or_create(product=product,subscription_content=subContent,defaults={'amount': 0,'product':product,'subscription_content':subContent})
                subItem.amount = request.POST.get("amount"+str(product.id),0)
                subItem.save()
            returnValues['saved'] = True
        else:
            returnValues['error'] = error
    for prod in products:
        try:
            subItem = SubscriptionContentFutureItem.objects.get(subscription_content=subContent.id,product=prod)
            prod.amount_in_subscription = subItem.amount
        except SubscriptionContentFutureItem.DoesNotExist:
            prod.amount_in_subscription = 0
        for type in subscription.future_types.all():
            if(SubscriptionSizeMandatoryProducts.objects.filter(product=prod,subscription_size=type.size).exists()):
                prod.min_amount = SubscriptionSizeMandatoryProducts.objects.get(product=prod,subscription_size=type.size).amount
            else:
                prod.min_amount = 0

    returnValues['subscription'] = subscription
    returnValues['products'] = products
    returnValues['subscription_size'] = int(calculate_current_size(subscription))
    returnValues['future_subscription_size'] = int(calculate_future_size(subscription))
    return render(request, 'cs/subscription_content_edit.html',returnValues)

@permission_required('juntagrico.is_operations_group')
def contentchangelist(request,subscription_id=None):
    render_dict = get_menu_dict(request)
    render_dict.update(get_changedate(request))
    changedlist = []
    subscriptions_list = SubscriptionDao.all_active_subscritions()
    for subscription in subscriptions_list:
        if subscription.content.content_changed:
            changedlist.append(subscription)
    return subscription_management_list(changedlist,render_dict, 'cs/contentchangelist.html',request)

@permission_required('juntagrico.is_operations_group')
def activate_future_content(request, subscription_id):
    subscription = get_object_or_404(Subscription, id=subscription_id)
    for content in subscription.content.products.all():
        content.delete()
    for content in subscription.content.future_products.all():
        SubscriptionContentItem.objects.create(subscription_content=subscription.content,amount=content.amount,product=content.product)
    return return_to_previous_location(request)