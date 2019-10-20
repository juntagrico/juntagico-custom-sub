import logging

from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import get_object_or_404, redirect, render, reverse
from django.utils import timezone

from juntagrico import mailer as ja_mailer
from juntagrico import models as jm
from juntagrico.config import Config
from juntagrico.dao.subscriptiondao import SubscriptionDao
from juntagrico.decorators import create_subscription_session, primary_member_of_subscription
from juntagrico.models import Subscription, SubscriptionProduct
from juntagrico.util import management as ja_mgmt
from juntagrico.util import return_to_previous_location, sessions, temporal
from juntagrico.util.form_evaluation import selected_subscription_types
from juntagrico.util.management import replace_subscription_types
from juntagrico.util.management_list import get_changedate
from juntagrico.util.views_admin import subscription_management_list
from juntagrico.views import get_menu_dict
from juntagrico.views_create_subscription import CSSummaryView, cs_finish
from juntagrico_custom_sub.models import (
    Product, SubscriptionContent, SubscriptionContentFutureItem, SubscriptionContentItem,
    SubscriptionSizeMandatoryProducts
)
from juntagrico_custom_sub.util.sub_content import calculate_current_size, calculate_future_size, new_content_valid

logger = logging.getLogger(__name__)


########################################################################################################################
# Monkey patching CSSessionObject
#
# Adds the custom product selectin to the signup process
########################################################################################################################
old_init = sessions.CSSessionObject.__init__


def new_init(self):
    old_init(self)
    self.custom_prod = {}


sessions.CSSessionObject.__init__ = new_init


def new_next_page(self):
    has_subs = self.subscription_size() > 0
    if not self.subscriptions:
        return 'cs-subscription'
    elif has_subs and not self.custom_prod:
        return 'custom_sub_initial_select'
    elif has_subs and not self.depot:
        return 'cs-depot'
    elif has_subs and not self.start_date:
        return 'cs-start'
    elif has_subs and not self.co_members_done:
        return 'cs-co-members'
    elif not self.evaluate_ordered_shares():
        return 'cs-shares'
    return 'cs-summary'


sessions.CSSessionObject.next_page = new_next_page


########################################################################################################################
class CustomCSSummaryView(CSSummaryView):
    """
    Custom summary view for custom products.
    Overwrites post method to make sure custom products are added to the subscription
    """
    @staticmethod
    def post(request, cs_session):
        # create subscription
        subscription = None
        if sum(cs_session.subscriptions.values()) > 0:
            subscription = ja_mgmt.create_subscription(
                cs_session.start_date, cs_session.depot, cs_session.subscriptions
                )

        # create and/or add members to subscription and create their shares
        ja_mgmt.create_or_update_member(cs_session.main_member, subscription, cs_session.main_member.new_shares)
        for co_member in cs_session.co_members:
            ja_mgmt.create_or_update_member(co_member, subscription, co_member.new_shares, cs_session.main_member)

        # set primary member of subscription
        if subscription is not None:
            subscription.primary_member = cs_session.main_member
            subscription.save()
            ja_mailer.send_subscription_created_mail(subscription)

        # associate custom products with subscription
        if subscription is not None:
            add_products_to_subscription(subscription.id, cs_session.custom_prod)
        # finish registration
        return cs_finish(request)


@create_subscription_session
def custom_cs_select_subscription(request, cs_session):
    if request.method == 'POST':
        # create dict with subscription type -> selected amount
        selected = selected_subscription_types(request.POST)
        cs_session.subscriptions = selected
        if (not quantity_error(selected)):
            return redirect(cs_session.next_page())
    render_dict = {
        'quantity_error': False if not (request.method == 'POST') else quantity_error(selected),
        'selected_subscriptions': cs_session.subscriptions,
        'hours_used': Config.assignment_unit() == 'HOURS',
        'products': SubscriptionProduct.objects.all(),
    }
    return render(request, 'cs/custom_select_subscription.html', render_dict)


@primary_member_of_subscription
def size_change(request, subscription_id):
    """
    change the size of a subscription
    overwrites original method in juntagrico
    implements stricter validation
    """
    subscription = get_object_or_404(Subscription, id=subscription_id)
    saved = False
    share_error = False
    if request.method == 'POST' and int(timezone.now().strftime('%m')) <= Config.business_year_cancelation_month():
        # create dict with subscription type -> selected amount
        selected = selected_subscription_types(request.POST)
        # check if members of sub have enough shares
        if subscription.all_shares < sum([sub_type.shares * amount for sub_type, amount in selected.items()]):
            share_error = True
        elif (not quantity_error(selected)):
            replace_subscription_types(subscription, selected)
            saved = True
    products = jm.SubscriptionProduct.objects.all()
    renderdict = get_menu_dict(request)
    renderdict.update({
        'saved': saved,
        'subscription': subscription,
        'quantity_error': quantity_error(selected) if (request.method == 'POST') else False,
        'shareerror': share_error,
        'hours_used': Config.assignment_unit() == 'HOURS',
        'next_cancel_date': temporal.next_cancelation_date(),
        'selected_subscription': subscription.future_types.all()[0].id,
        'products': products,
    })
    return render(request, 'cs/custom_size_change.html', renderdict)


def quantity_error(selected):
    """
    validates the selected quantities for Basimilch's usecase
    selected is a dictionnary with SubscriptionType objects as keys and amounts as values
    """
    total_liters = sum([x * y for x, y in zip(selected.values(), [4, 8, 2])])
    if total_liters < 4:
        return True
    selected4 = selected[jm.SubscriptionType.objects.get(size__units=4)]
    selected8 = selected[jm.SubscriptionType.objects.get(size__units=8)]
    required8 = total_liters // 8
    required4 = (total_liters % 8) // 4
    return not(selected8 == required8 and selected4 == required4)


@primary_member_of_subscription
def subscription_content_edit(request, subscription_id=None):
    returnValues = dict()
    # subscription_id cannot be none --> route not defined
    # member = request.user.member
    # if subscription_id is None:
    #     subscription = member.subscription
    # else:
    subscription = get_object_or_404(Subscription, id=subscription_id)
    # subscription content should always exists in this view --> no try, exist?
    try:
        subContent = SubscriptionContent.objects.get(subscription=subscription)
    except SubscriptionContent.DoesNotExist:
        subContent = SubscriptionContent.objects.create(subscription=subscription)
        subContent.save()
        for type in subscription.future_types.all():
            for mandatoryProd in SubscriptionSizeMandatoryProducts.objects.filter(subscription_size=type.size):
                subItem = SubscriptionContentFutureItem.objects.create(
                    subscription_content=subContent,
                    product=mandatoryProd.product,
                    amount=mandatoryProd.amount
                    )
                subItem.save()
    products = Product.objects.all().order_by('user_editable')
    if "saveContent" in request.POST:
        valid, error = new_content_valid(subscription, request, products)
        if valid:
            for product in products:
                subItem, p = SubscriptionContentFutureItem.objects.get_or_create(
                    product=product,
                    subscription_content=subContent,
                    defaults={'amount': 0, 'product': product, 'subscription_content': subContent}
                    )
                subItem.amount = request.POST.get("amount"+str(product.id), 0)
                subItem.save()
            q = '?saved'
        else:
            q = f'?error={error}'
        return redirect(f"{reverse('content_edit_result')}{q}&subs_id={subscription_id}")
    subs_sizes = count_subs_sizes(subscription.future_types.all())
    for prod in products:
        sub_item = SubscriptionContentFutureItem.objects.filter(
            subscription_content=subContent.id, product=prod
            ).first()
        chosen_amount = 0 if not sub_item else sub_item.amount
        min_amount = determine_min_amount(prod, subs_sizes)
        prod.min_amount = min(min_amount, chosen_amount)
        prod.amount_in_subscription = max(chosen_amount, min_amount)

    returnValues['subscription'] = subscription
    returnValues['products'] = products
    returnValues['subscription_size'] = int(calculate_current_size(subscription))
    returnValues['future_subscription_size'] = int(calculate_future_size(subscription))
    return render(request, 'cs/subscription_content_edit.html', returnValues)


@login_required
def content_edit_result(request, subscription_id=None):
    rv = {}
    if 'saved' in request.GET:
        rv['saved'] = True
    elif 'error' in request.GET:
        rv['error'] = request.GET.get('error')
    rv['subs_id'] = request.GET.get('subs_id')
    return render(request, 'cs/content_edit_result.html', rv)


@create_subscription_session
def custom_sub_initial_select(request, cs_session):
    if request.method == 'POST':
        # create dict with subscription type -> selected amount
        custom_prod = selected_custom_products(request.POST)
        cs_session.custom_prod = custom_prod
        return redirect(cs_session.next_page())
    products = Product.objects.all().order_by('user_editable')
    subs_sizes = {t.size: a for t, a in cs_session.subscriptions.items() if a > 0}
    for p in products:
        p.min_amount = determine_min_amount(p, subs_sizes)
        p.amount_in_subscription = p.min_amount

    returnValues = {}
    returnValues['products'] = products
    returnValues['subscription_size'] = int(cs_session.subscription_size())
    returnValues['future_subscription_size'] = int(cs_session.subscription_size())
    return render(request, 'cs/subscription_content_edit.html', returnValues)


def add_products_to_subscription(subscription_id, custom_products):
    """
    adds custom products to the subscription with the given id.
    custom_prodducts is a dictionary with the product object as keys and their
    amount as value.
    """
    content = SubscriptionContent(subscription_id=subscription_id)
    content.save()
    selected_items = {
        p: a for p, a in custom_products.items() if a != 0
        }
    for prod, amount in selected_items.items():
        item = SubscriptionContentItem(
            amount=amount,
            product_id=prod.id,
            subscription_content_id=content.id
            )
        item.save()


def determine_min_amount(product, subs_sizes):
    '''
    Given a product and a dictionary of subscription sizes, return the minimum (mandatory) amount.
    Allows for situations where a product can be mandatory for more than one size.
    Example of subs_sizes: {size_1: amount_1, size_2: amount_2, etc...}
    '''
    min_amount = 0
    for size, count in subs_sizes.items():
        mand_prod = SubscriptionSizeMandatoryProducts.objects.filter(product=product, subscription_size=size).first()
        if mand_prod:
            min_amount += mand_prod.amount * count
    return min_amount


def count_subs_sizes(subs_types):
    rv = {}
    for st in subs_types:
        if st.size.id not in rv:
            rv[st.size.id] = 1
        else:
            rv[st.size.id] += 1
    return rv


def selected_custom_products(post_data):
    return {
        prod: int(
            post_data.get(f'amount{prod.id}', 0)
        ) for prod in Product.objects.all()
    }


@permission_required('juntagrico.is_operations_group')
def contentchangelist(request, subscription_id=None):
    render_dict = get_menu_dict(request)
    render_dict.update(get_changedate(request))
    changedlist = []
    subscriptions_list = SubscriptionDao.all_active_subscritions()
    for subscription in subscriptions_list:
        if subscription.content.content_changed:
            changedlist.append(subscription)
    return subscription_management_list(changedlist, render_dict, 'cs/contentchangelist.html', request)


@permission_required('juntagrico.is_operations_group')
def activate_future_content(request, subscription_id):
    subscription = get_object_or_404(Subscription, id=subscription_id)
    for content in subscription.content.products.all():
        content.delete()
    for content in subscription.content.future_products.all():
        SubscriptionContentItem.objects.create(
            subscription_content=subscription.content, amount=content.amount, product=content.product
            )
    return return_to_previous_location(request)
