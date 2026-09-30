# -*- coding: utf-8 -*-

import json

from odoo.http import request, route
from odoo.addons.website_sale.controllers.main import WebsiteSale


class WebsiteSaleCustomerDedup(WebsiteSale):

    # ---------------------------------------------------------
    # PHONE
    # ---------------------------------------------------------

    def _normalize_phone(self, phone):
        """
        Normalize Bangladeshi phone numbers.

        Examples:

        01712345678
        +8801712345678
        8801712345678

        All become:

        01712345678
        """

        if not phone:
            return False

        # Keep digits only
        phone = ''.join(
            char for char in phone
            if char.isdigit()
        )

        # 8801712345678 -> 01712345678
        if phone.startswith('880') and len(phone) == 13:
            phone = '0' + phone[3:]

        return phone

    def _find_existing_customer_by_phone(self, phone):
        """
        Find an existing MAIN contact using phone/mobile.

        Email is intentionally NOT used for matching.
        """

        normalized_phone = self._normalize_phone(phone)

        if not normalized_phone:
            return request.env['res.partner']

        Partner = request.env['res.partner'].sudo()

        # Only search main contacts.
        partners = Partner.search([
            ('parent_id', '=', False),
            '|',
            ('phone', '!=', False),
            ('mobile', '!=', False),
        ])

        for partner in partners:

            phone_number = self._normalize_phone(
                partner.phone
            )

            mobile_number = self._normalize_phone(
                partner.mobile
            )

            if normalized_phone in (
                phone_number,
                mobile_number,
            ):
                return partner

        return Partner

    # ---------------------------------------------------------
    # ADDRESS
    # ---------------------------------------------------------

    def _normalize_address_value(self, value):
        """
        Normalize textual address values before comparison.
        """

        if not value:
            return ''

        return ' '.join(
            str(value).strip().lower().split()
        )

    def _address_signature(self, values):
        """
        Generate a signature representing a physical address.

        We intentionally DO NOT compare:
            name
            email
            phone

        because those don't define whether the physical
        delivery address is the same.
        """

        return (
            self._normalize_address_value(
                values.get('street')
            ),
            self._normalize_address_value(
                values.get('street2')
            ),
            self._normalize_address_value(
                values.get('city')
            ),
            self._normalize_address_value(
                values.get('zip')
            ),
            values.get('state_id') or False,
            values.get('country_id') or False,
        )

    def _find_existing_child_address(
        self,
        customer,
        address_values,
    ):
        """
        Find whether this customer already has the
        same delivery address.
        """

        Partner = request.env['res.partner'].sudo()

        wanted_signature = self._address_signature(
            address_values
        )

        addresses = Partner.search([
            ('parent_id', '=', customer.id),
            ('type', 'in', [
                'delivery',
                'invoice',
                'other',
            ]),
        ])

        for address in addresses:

            existing_signature = (
                self._address_signature({
                    'street': address.street,
                    'street2': address.street2,
                    'city': address.city,
                    'zip': address.zip,
                    'state_id': (
                        address.state_id.id
                        if address.state_id
                        else False
                    ),
                    'country_id': (
                        address.country_id.id
                        if address.country_id
                        else False
                    ),
                })
            )

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

        # -----------------------------------------------------
        # Only apply custom behavior for guest checkout.
        # Logged-in users continue through standard Odoo logic.
        # -----------------------------------------------------

        if (
            order_sudo
            and order_sudo._is_anonymous_cart()
        ):

            phone = (
                form_data.get('phone')
                or form_data.get('mobile')
            )

            existing_customer = (
                self._find_existing_customer_by_phone(
                    phone
                )
            )

            # -------------------------------------------------
            # EXISTING CUSTOMER FOUND
            # -------------------------------------------------

            if existing_customer:

                # Let Odoo convert fields such as:
                #
                # country_id
                # state_id
                #
                # into proper ORM values.

                address_values, _extra = (
                    self._parse_form_data(
                        form_data
                    )
                )

                # ---------------------------------------------
                # Check whether this exact address already
                # exists under the customer.
                # ---------------------------------------------

                existing_address = (
                    self._find_existing_child_address(
                        existing_customer,
                        address_values,
                    )
                )

                # ---------------------------------------------
                # SAME ADDRESS
                # ---------------------------------------------

                if existing_address:

                    delivery_address = existing_address

                # ---------------------------------------------
                # NEW ADDRESS
                # ---------------------------------------------

                else:

                    child_values = address_values.copy()

                    child_values.update({
                        'parent_id': existing_customer.id,
                        'type': 'delivery',
                    })

                    # Main customer information must NOT
                    # be overwritten by checkout data.

                    if not child_values.get('name'):
                        child_values['name'] = (
                            existing_customer.name
                        )

                    delivery_address = (
                        request.env['res.partner']
                        .sudo()
                        .create(child_values)
                    )

                # ---------------------------------------------
                # Link order with MAIN existing customer
                # ---------------------------------------------

                order_sudo._update_address(
                    existing_customer.id,
                    {'partner_id'},
                )

                # ---------------------------------------------
                # Set delivery address
                # ---------------------------------------------

                order_sudo._update_address(
                    delivery_address.id,
                    {'partner_shipping_id'},
                )

                # ---------------------------------------------
                # Billing = delivery
                #
                # Current project requirement:
                # use same address for billing.
                # ---------------------------------------------

                order_sudo._update_address(
                    delivery_address.id,
                    {'partner_invoice_id'},
                )

                # ---------------------------------------------
                # Remove public website partner follower
                # ---------------------------------------------

                order_sudo.message_unsubscribe(
                    order_sudo.website_id.partner_id.ids
                )

                return json.dumps({
                    'redirectUrl': (
                        callback
                        or '/shop/checkout?try_skip_step=true'
                    ),
                })

        # -----------------------------------------------------
        # NEW PHONE
        #
        # No existing customer was found.
        #
        # Let Odoo perform its NORMAL checkout.
        # Odoo will create the new customer.
        # -----------------------------------------------------

        return super().shop_address_submit(
            partner_id=partner_id,
            address_type=address_type,
            use_delivery_as_billing=use_delivery_as_billing,
            callback=callback,
            required_fields=required_fields,
            **form_data
        )