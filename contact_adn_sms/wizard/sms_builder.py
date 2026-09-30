from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SmsBuilder(models.TransientModel):
    _inherit = "sms.builder"

    contact_id = fields.Many2one("res.partner", string="Contact", readonly=True)

    @api.model
    def default_get(self, field_list):
        values = super().default_get(field_list)
        context = self.env.context

        partner_id = (
            values.get("partner_id")
            or context.get("default_partner_id")
            or (
                context.get("active_id")
                if context.get("active_model") == "res.partner"
                else False
            )
        )
        if partner_id:
            values.setdefault("partner_id", partner_id)
            values.setdefault("contact_id", partner_id)

        if "sms_account" in field_list and not values.get("sms_account"):
            adn_account = self.env["betopia.sms.account"].search(
                [("gateway_id.gateway", "=", "adn_sms")],
                limit=1,
            )
            if adn_account:
                values["sms_account"] = adn_account.id

        return values

    def action_confirm_sms(self):
        self.ensure_one()
        account = (
            self.sms_account
            or (self.template_id and self.template_id.sms_account)
        )
        if not account or account.gateway_id.gateway != "adn_sms":
            raise ValidationError(
                _("Please select an SMS Account configured with the ADN gateway.")
            )

        # The base Betopia wizard stores self.sms_account on the message record.
        # Keep it populated when the account came from an SMS template.
        self.sms_account = account
        result = super().action_confirm_sms()

        if result.get("params", {}).get("type") == "success" and self.contact_id:
            self.contact_id.message_post(
                body=Markup(
                    "<strong>%s</strong><br/>%s"
                )
                % (_("SMS sent via ADN"), self.text_message or ""),
                subtype_xmlid="mail.mt_note",
            )

        return result
