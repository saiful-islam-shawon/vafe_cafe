# -*- coding: utf-8 -*-

import re

from odoo import api, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    @api.model
    def _zencore_normalize_customer_phone(self, phone):
        """Return a comparable Bangladesh mobile number (01XXXXXXXXX)."""
        if not phone:
            return False

        digits = re.sub(r'\D', '', str(phone))

        # +8801XXXXXXXXX / 8801XXXXXXXXX -> 01XXXXXXXXX
        if digits.startswith('880') and len(digits) == 13:
            digits = '0' + digits[3:]
        # 1XXXXXXXXX -> 01XXXXXXXXX (defensive support for normalized values)
        elif len(digits) == 10 and digits.startswith('1'):
            digits = '0' + digits

        return digits

    @api.model
    def _zencore_find_main_partner_by_phone(self, phone):
        """Find an existing main contact by phone/mobile only."""
        normalized = self._zencore_normalize_customer_phone(phone)
        Partner = self.env['res.partner'].sudo()

        if not normalized:
            return Partner

        partners = Partner.search([
            ('parent_id', '=', False),
            '|',
            ('phone', '!=', False),
            ('mobile', '!=', False),
        ])

        for partner in partners:
            if normalized in (
                self._zencore_normalize_customer_phone(partner.phone),
                self._zencore_normalize_customer_phone(partner.mobile),
            ):
                return partner

        return Partner

    @api.model_create_multi
    def create(self, vals_list):
        """
        Reuse an existing main contact during signup/user creation when the
        signup login is a phone number already present on a contact.

        Authentication/OTP behavior is intentionally left to the existing
        meta_otp_auth module. This method only supplies partner_id so Odoo
        links the new user to the existing contact instead of creating a
        duplicate contact.
        """
        for vals in vals_list:
            # Respect any flow that already explicitly selected a partner.
            if vals.get('partner_id'):
                continue

            login = vals.get('login')
            normalized_login = self._zencore_normalize_customer_phone(login)

            # Only treat the login as a phone when it resolves to a standard
            # Bangladeshi mobile number. Email/other login flows remain intact.
            if not (
                normalized_login
                and len(normalized_login) == 11
                and normalized_login.startswith('01')
                and normalized_login[2:3] in tuple('3456789')
            ):
                continue

            existing_partner = self._zencore_find_main_partner_by_phone(login)
            if existing_partner:
                vals['partner_id'] = existing_partner.id

        return super().create(vals_list)
