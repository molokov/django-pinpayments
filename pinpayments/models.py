"""
Models for interacting with Pin, and storing results
"""

from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.timezone import get_default_timezone
from django.utils.translation import gettext_lazy as _

from .exceptions import ConfigError, PinError
from .objects import PinEnvironment
from .utils import get_value


if getattr(settings, 'PIN_ENVIRONMENTS', {}) == {}:
    raise ConfigError("PIN_ENVIRONMENTS not defined.")


TRANS_TYPE_CHOICES = (
    ('Payment', 'Payment'),
    ('Refund', 'Refund'),
)

CARD_TYPES = (
    ('master', 'Mastercard'),
    ('visa', 'Visa'),
)


class CustomerToken(models.Model):
    """
    A token returned by the Pin Payments Customer API.
    These can be used on a Transaction in lieu of of a Card token, and
    can be reused.
    They are linked to a User record and are typically used for recurring
    billing.
    Card token - difference is that a card can only be used once, for a transaction
    or to be converted to a Customer token. Customer tokens can be reused.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    environment = models.CharField(
        max_length=25, db_index=True, blank=True,
        help_text=_('The name of the Pin environment to use, eg test or live.')
    )
    token = models.CharField(
        _('Token'), max_length=100,
        help_text=_('Generated by Card API or Customers API')
    )
    created = models.DateTimeField(_('Created'), auto_now_add=True)
    active = models.BooleanField(_('Active'), default=True)
    card_type = models.CharField(
        _('Card Type'), max_length=20, blank=True, null=True,
        choices=CARD_TYPES, help_text=_('Determined automatically by Pin')
    )
    card_number = models.CharField(
        _('Card Number'), max_length=100, blank=True, null=True,
        help_text=_('Cleansed by Pin API')
    )
    card_name = models.CharField(
        _('Name on Card'), max_length=100, blank=True, null=True
    )

    def __str__(self):
        return "{0}".format(self.token)

    def save(self, *args, **kwargs):
        if not self.environment:
            self.environment = getattr(settings, 'PIN_DEFAULT_ENVIRONMENT', 'test')
        super(CustomerToken, self).save(*args, **kwargs)

    def new_card_token(self, card_token):
        """ Placeholder to retain name and functionality of old method """
        self.update_card(card_token)
        return True

    def update_card(self, card_token):
        """ Provide a card token to update the details for this customer """
        pin_env = PinEnvironment(self.environment)
        payload = {'card_token': card_token}
        url_tail = "/customers/{1}".format(self.token)
        data = pin_env.pin_put(url_tail, payload)[1]['response']
        self.card_number = data['card']['display_number']
        self.card_type = data['card']['scheme']
        self.card_name = data['card']['name']
        self.save()

    @classmethod
    def create_from_card_token(cls, card_token, user, environment=''):
        """ Create a customer token from a card token """
        pin_env = PinEnvironment(environment)
        payload = {'email': user.email, 'card_token': card_token}
        data = pin_env.pin_post("/customers", payload)[1]['response']
        customer = CustomerToken.objects.create(
            user=user,
            token=data['token'],
            environment=environment,
            card_number=data['card']['display_number'],
            card_type=data['card']['scheme'],
            card_name=data['card']['name'],
        )
        return customer


class PinTransaction(models.Model):
    """
    PinTransaction - model to hold response data from the pin.net.au
    Charge API. Note we capture the card and/or customer token, but
    there's no FK to your own customers table. That's for you to do
    in your own code.
    """
    date = models.DateTimeField(
        _('Date'), db_index=True, help_text=_(
            'Time this transaction was put in the database. '
            'May differ from the time that PIN reports the transaction.'
        )
    )
    environment = models.CharField(
        max_length=25, db_index=True, blank=True,
        help_text=_('The name of the Pin environment to use, eg test or live.')
    )
    amount = models.DecimalField(
        _('Amount (Dollars)'), max_digits=10, decimal_places=2
    )
    fees = models.DecimalField(
        _('Transaction Fees'), max_digits=10, decimal_places=2,
        default=Decimal("0.00"), blank=True, null=True, help_text=_(
            'Fees charged to you by Pin, for this transaction, in dollars'
        )
    )
    description = models.TextField(
        _('Description'), blank=True, null=True,
        help_text=_('As provided when you initiated the transaction')
    )
    processed = models.BooleanField(
        _('Processed?'), default=False,
        help_text=_('Has this been sent to Pin yet?')
    )
    succeeded = models.BooleanField(
        _('Success?'), default=False,
        help_text=_('Was the transaction approved?')
    )
    currency = models.CharField(
        _('Currency'), max_length=100, default='AUD',
        help_text=_('Currency transaction was processed in')
    )
    transaction_token = models.CharField(
        _('Pin API Transaction Token'), max_length=100, blank=True, null=True,
        db_index=True, help_text=_('Unique ID from Pin for this transaction')
    )
    card_token = models.CharField(
        _('Pin API Card Token'), max_length=40, blank=True, null=True,
        help_text=_(
            'Card token used for this transaction (Card API and Web Forms)'
        )
    )
    customer_token = models.ForeignKey(
        CustomerToken, blank=True, null=True,
        help_text=_('Provided by Customer API'),
        on_delete=models.SET_NULL,
    )
    pin_response = models.CharField(
        _('API Response'), max_length=255, blank=True, null=True,
        help_text=_('Response text, usually Success!')
    )
    ip_address = models.GenericIPAddressField(
        help_text=_('IP Address used for payment')
    )
    email_address = models.EmailField(
        _('E-Mail Address'), max_length=100, help_text=_('As passed to Pin.')
    )
    card_address1 = models.CharField(
        _('Cardholder Street Address'), max_length=100, blank=True, null=True,
        help_text=_('Address entered by customer to process this transaction')
    )
    card_address2 = models.CharField(
        _('Cardholder Street Address Line 2'), max_length=100, blank=True,
        null=True
    )
    card_city = models.CharField(
        _('Cardholder City'), max_length=100, blank=True, null=True
    )
    card_state = models.CharField(
        _('Cardholder State'), max_length=100, blank=True, null=True
    )
    card_postcode = models.CharField(
        _('Cardholder Postal / ZIP Code'), max_length=100, blank=True,
        null=True
    )
    card_country = models.CharField(
        _('Cardholder Country'), max_length=100, blank=True, null=True
    )
    card_number = models.CharField(
        _('Card Number'), max_length=100, blank=True, null=True,
        help_text=_('Cleansed by Pin API')
    )
    card_type = models.CharField(
        _('Card Type'), max_length=20, blank=True, null=True,
        choices=CARD_TYPES, help_text=_('Determined automatically by Pin')
    )
    pin_response_text = models.TextField(
        _('Complete API Response'), blank=True, null=True,
        help_text=_('The full JSON response from the Pin API')
    )

    def save(self, *args, **kwargs):
        if not (self.card_token or self.customer_token):
            raise PinError("Must provide card_token or customer_token")

        if self.card_token and self.customer_token:
            raise PinError("Can only provide card_token OR customer_token, not both")

        if not self.environment:
            self.environment = getattr(settings, 'PIN_DEFAULT_ENVIRONMENT', 'test')

        if self.environment not in getattr(settings, 'PIN_ENVIRONMENTS', {}):
            raise PinError("Pin Environment '{0}' does not exist".format(self.environment))

        if not self.date:
            now = datetime.now()
            if settings.USE_TZ:
                now = timezone.make_aware(now, get_default_timezone())
            self.date = now

        super(PinTransaction, self).save(*args, **kwargs)

    def __str__(self):
        return "{0}".format(self.id)

    class Meta:
        verbose_name = 'PIN.net.au Transaction'
        verbose_name_plural = 'PIN.net.au Transactions'
        ordering = ['-date']

    def process_transaction(self):
        """ Send the data to Pin for processing """
        if self.processed:
            return None  # can only attempt to process once.
        self.processed = True
        self.save()

        pin_env = PinEnvironment(self.environment)
        payload = {
            'email': self.email_address,
            'description': self.description,
            'amount': int(self.amount * 100),
            'currency': self.currency,
            'ip_address': self.ip_address,
        }
        if self.card_token:
            payload['card_token'] = self.card_token
        else:
            payload['customer_token'] = self.customer_token.token

        response, response_json = pin_env.pin_post('/charges', payload, True)
        self.pin_response_text = response.text

        if response_json is None:
            self.pin_response = 'Failure.'
        elif 'error' in response_json.keys():
            if 'messages' in response_json.keys():
                if 'message' in response_json['messages'][0].keys():
                    self.pin_response = 'Failure: {0}'.format(
                        response_json['messages'][0]['message']
                    )
            else:
                self.pin_response = 'Failure: {0}'.format(
                    response_json['error_description']
                )
            self.transaction_token = response_json.get('charge_token', None)
        else:
            data = response_json['response']
            self.succeeded = True
            self.transaction_token = data['token']
            self.fees = data['total_fees'] / Decimal("100.00")
            self.pin_response = data['status_message']
            self.card_address1 = data['card']['address_line1']
            self.card_address2 = data['card']['address_line2']
            self.card_city = data['card']['address_city']
            self.card_state = data['card']['address_state']
            self.card_postcode = data['card']['address_postcode']
            self.card_country = data['card']['address_country']
            self.card_number = data['card']['display_number']
            self.card_type = data['card']['scheme']

        self.save()
        return self.pin_response


class BankAccount(models.Model):
    """ A representation of a bank account, as stored by Pin. """
    token = models.CharField(
        _('Pin API Bank account token'), max_length=40, db_index=True,
        help_text=_("A bank account token provided by Pin")
    )
    bank_name = models.CharField(
        _('Bank Name'), max_length=100,
        help_text=_("The name of the bank at which this account is held")
    )
    branch = models.CharField(
        _('Branch name'), max_length=100, blank=True,
        help_text=_("The name of the branch at which this account is held")
    )
    name = models.CharField(
        _('Recipient Name'), max_length=100,
        help_text="The name of the bank account"
    )
    bsb = models.IntegerField(
        _('BSB'),
        help_text=_("The BSB (Bank State Branch) code of the bank account.")
    )
    number = models.CharField(
        _('BSB'), max_length=20,
        help_text=_("The account number of the bank account")
    )
    environment = models.CharField(
        max_length=25, db_index=True, blank=True,
        help_text=_('The name of the Pin environment to use, eg test or live.')
    )

    def __str__(self):
        return "{0}".format(self.token)


class PinRecipient(models.Model):
    """
    A recipient stored for the purpose of having funds transferred to them
    """
    token = models.CharField(
        max_length=40, db_index=True,
        help_text=_("A recipient token provided by Pin")
    )
    email = models.EmailField(max_length=100, help_text=_('As passed to Pin.'))
    name = models.CharField(
        max_length=100, blank=True, null=True,
        help_text=_("Optional. The name by which the recipient is referenced")
    )
    created = models.DateTimeField(_("Time created"), auto_now_add=True)
    bank_account = models.ForeignKey(
        BankAccount, blank=True, null=True,
        on_delete=models.SET_NULL,
    )
    environment = models.CharField(
        max_length=25, db_index=True, blank=True,
        help_text=_('The name of the Pin environment to use, eg test or live.')
    )

    def __str__(self):
        return "{0}".format(self.token)

    @classmethod
    def create_with_bank_account(cls, email, account_name, bsb, number, name=""):
        """ Creates a new recipient from a provided bank account's details """
        pin_env = PinEnvironment()
        payload = {
            'email': email,
            'name': name,
            'bank_account[name]': account_name,
            'bank_account[bsb]': bsb,
            'bank_account[number]': number
        }
        data = pin_env.pin_post('/recipients', payload)[1]['response']
        bank_account = BankAccount.objects.create(
            bank_name=data['bank_account']['bank_name'],
            branch=data['bank_account']['branch'],
            bsb=data['bank_account']['bsb'],
            name=data['bank_account']['name'],
            number=data['bank_account']['number'],
            token=data['bank_account']['token'],
            environment=pin_env.name,
        )
        new_recipient = cls.objects.create(
            token=data['token'],
            email=data['email'],
            name=data['name'],
            bank_account=bank_account,
            environment=pin_env.name,
        )
        return new_recipient


class PinTransfer(models.Model):
    """
    A transfer from a PinEnvironment to a PinRecipient
    """
    transfer_token = models.CharField(
        _('Pin API Transfer Token'), max_length=100, blank=True, null=True,
        db_index=True, help_text=_('Unique ID from Pin for this transfer')
    )
    status = models.CharField(
        max_length=100, blank=True, null=True,
        help_text=_("Status of transfer at time of saving")
    )
    currency = models.CharField(
        max_length=10, help_text=_("currency of transfer")
    )
    description = models.CharField(
        max_length=100, blank=True, null=True,
        help_text=_("Description as shown on statement")
    )
    amount = models.IntegerField(help_text=_(
        "Transfer amount, in the base unit of the "
        "currency (e.g.: cents for AUD, yen for JPY)"
    ))
    recipient = models.ForeignKey(PinRecipient, blank=True, null=True, on_delete=models.SET_NULL)
    created = models.DateTimeField(auto_now_add=True)
    pin_response_text = models.TextField(
        _('Complete API Response'), blank=True, null=True,
        help_text=_('The full JSON response from the Pin API')
    )

    def __str__(self):
        return "{0}".format(self.transfer_token)

    @property
    def value(self):
        """
        Returns the value of the transfer in the representation of the
        currency it is in, without symbols
        That is, 1000 cents as 10.00, 1000 yen as 1000
        """
        return get_value(self.amount, self.currency)

    @classmethod
    def send_new(cls, amount, description, recipient, currency="AUD"):
        """ Creates a transfer by sending it to Pin """
        pin_env = PinEnvironment()
        payload = {
            'amount': amount,
            'description': description,
            'recipient': recipient.token,
            'currency': currency,
        }
        response, response_json = pin_env.pin_post('/transfers', payload)
        data = response_json['response']
        new_transfer = PinTransfer.objects.create(
            transfer_token=data['token'],
            status=data['status'],
            currency=data['currency'],
            description=data['description'],
            amount=data['amount'],
            recipient=recipient,
            pin_response_text=response.text,
        )
        return new_transfer
