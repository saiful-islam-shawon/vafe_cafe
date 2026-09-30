# -*- coding: utf-8 -*-

import json

from odoo import _
from odoo.exceptions import UserError
from odoo.http import request, route
from odoo.addons.auth_signup.controllers.main import AuthSignupHome
from odoo.addons.website_sale.controllers.main import WebsiteSale


class VafeCafePhoneMixin:
    """Shared helpers for Bangladesh mobile/phone matching."""

    def _normalize_phone(self, phone):
        """
        Normalize common Bangladeshi phone formats to 01XXXXXXXXX.

        Examples:
            01712345678    -> 01712345678
            +8801712345678 -> 01712345678
            8801712345678  -> 01712345678
            1712345678     -> 01712345678
        """
        if not phone:
            return False

        digits = ''.join(char for char in str(phone) if char.isdigit())

        if digits.startswith('880') and len(digits) == 13:
            digits = '0' + digits[3:]
        elif len(digits) == 10 and digits.startswith('1'):
            digits = '0' + digits

        return digits

    def _find_existing_customer_by_phone(self, phone):
        """Find an existing main contact by normalized phone/mobile."""
        normalized_phone = self._normalize_phone(phone)
        Partner = request.env['res.partner'].sudo()

        if not normalized_phone:
            return Partner

        # Main contacts only. Oldest matching contact wins if historical
        # duplicates already exist in the database.
        partners = Partner.search([
            ('parent_id', '=', False),
            '|',
            ('phone', '!=', False),
            ('mobile', '!=', False),
        ], order='id asc')

        for partner in partners:
            if normalized_phone in (
                self._normalize_phone(partner.phone),
                self._normalize_phone(partner.mobile),
            ):
                return partner

        return Partner


class VafeCafeAuthSignup(VafeCafePhoneMixin, AuthSignupHome):
    """
    Website signup customization.

    - Mobile is collected separately from the email/login field.
    - If the mobile already belongs to a main contact, reuse that contact.
    - Never overwrite the existing contact's name/email/phone/mobile/address.
    - If no matching contact exists, keep Odoo's normal signup behavior and
      store the submitted mobile on the newly created contact.
    """

    def _prepare_signup_values(self, qcontext):
        values = super()._prepare_signup_values(qcontext)

        mobile = request.params.get('mobile')
        if mobile:
            values['mobile'] = self._normalize_phone(mobile)

        return values

    def do_signup(self, qcontext):
        token = qcontext.get('token')

        # Invitation / reset-token signup must stay fully standard.
        if token:
            return super().do_signup(qcontext)

        values = self._prepare_signup_values(qcontext)
        mobile = values.get('mobile')

        existing_partner = self._find_existing_customer_by_phone(mobile)

        if not existing_partner:
            # New phone: normal Odoo signup creates a new partner/user.
            # Because _prepare_signup_values() added `mobile`, the new
            # partner also receives the submitted mobile number.
            return super().do_signup(qcontext)

        Users = request.env['res.users'].sudo()

        # One partner should not receive another website account.
        existing_user = Users.with_context(active_test=False).search([
            ('partner_id', '=', existing_partner.id),
        ], limit=1)

        if existing_user:
            raise UserError(_(
                "An account already exists for this mobile number. "
                "Please sign in or reset your password."
            ))

        login = values.get('login')
        password = values.get('password')

        if not login or not password:
            raise UserError(_("The signup form was not properly filled in."))

        # IMPORTANT: pass only user/account fields plus partner_id.
        # Do NOT pass name/email/mobile/address fields here, otherwise the
        # existing res.partner could be modified.
        signup_values = {
            'login': login,
            'password': password,
            'partner_id': existing_partner.id,
        }

        Users._signup_create_user(signup_values)

        # Match Odoo's standard signup behavior: commit before authentication,
        # because authenticate uses its own cursor.
        request.env.cr.commit()
        credential = {
            'login': login,
            'password': password,
            'type': 'password',
        }
        request.session.authenticate(request.db, credential)


class WebsiteSaleCustomerDedup(VafeCafePhoneMixin, WebsiteSale):

    # ---------------------------------------------------------
    # ADDRESS
    # ---------------------------------------------------------

    def _normalize_address_value(self, value):
        """Normalize textual address values before comparison."""
        if not value:
            return ''

        return ' '.join(str(value).strip().lower().split())

    def _address_signature(self, values):
        """
        Generate a signature representing a physical address.

        We intentionally DO NOT compare name, email, or phone because those
        values do not define whether the physical delivery address is the same.
        """
        return (
            self._normalize_address_value(values.get('street')),
            self._normalize_address_value(values.get('street2')),
            self._normalize_address_value(values.get('city')),
            self._normalize_address_value(values.get('zip')),
            values.get('state_id') or False,
            values.get('country_id') or False,
        )

    def _find_existing_child_address(self, customer, address_values):
        """Find whether this customer already has the same child address."""
        Partner = request.env['res.partner'].sudo()
        wanted_signature = self._address_signature(address_values)

        addresses = Partner.search([
            ('parent_id', '=', customer.id),
            ('type', 'in', ['delivery', 'invoice', 'other']),
        ])

        for address in addresses:
            existing_signature = self._address_signature({
                'street': address.street,
                'street2': address.street2,
                'city': address.city,
                'zip': address.zip,
                'state_id': address.state_id.id if address.state_id else False,
                'country_id': address.country_id.id if address.country_id else False,
            })

            if existing_signature == wanted_signature:
                return address

        return Partner

    # ---------------------------------------------------------
    # CHECKOUT
    # ---------------------------------------------------------

    @route()
    def shop_address_submit(
        self,
        partner_id=None,
        address_type='billing',
        use_delivery_as_billing=None,
        callback=None,
        required_fields=None,
        **form_data
    ):
        order_sudo = request.website.sale_get_order()

        # Apply custom behavior only for guest checkout.
        # Logged-in customers continue through standard Odoo logic.
        if order_sudo and order_sudo._is_anonymous_cart():
            phone = form_data.get('phone') or form_data.get('mobile')
            existing_customer = self._find_existing_customer_by_phone(phone)

            if existing_customer:
                # Let Odoo convert country_id/state_id and other form values
                # into the correct ORM representation.
                address_values, _extra = self._parse_form_data(form_data)

                existing_address = self._find_existing_child_address(
                    existing_customer,
                    address_values,
                )

                if existing_address:
                    delivery_address = existing_address
                else:
                    child_values = address_values.copy()
                    child_values.update({
                        'parent_id': existing_customer.id,
                        'type': 'delivery',
                    })

                    # Never overwrite the main contact with checkout data.
                    if not child_values.get('name'):
                        child_values['name'] = existing_customer.name

                    delivery_address = (
                        request.env['res.partner']
                        .sudo()
                        .create(child_values)
                    )

                # Keep the order on the MAIN contact. This is important for
                # later portal signup: once the portal user is linked to this
                # same contact, historical guest orders remain accessible.
                order_sudo._update_address(
                    existing_customer.id,
                    {'partner_id'},
                )

                order_sudo._update_address(
                    delivery_address.id,
                    {'partner_shipping_id'},
                )

                # Current project requirement: billing = delivery.
                order_sudo._update_address(
                    delivery_address.id,
                    {'partner_invoice_id'},
                )

                order_sudo.message_unsubscribe(
                    order_sudo.website_id.partner_id.ids
                )

                return json.dumps({
                    'redirectUrl': callback or '/shop/checkout?try_skip_step=true',
                })

        # New phone: let standard Odoo checkout create the customer/address.
        return super().shop_address_submit(
            partner_id=partner_id,
            address_type=address_type,
            use_delivery_as_billing=use_delivery_as_billing,
            callback=callback,
            required_fields=required_fields,
            **form_data
        )
