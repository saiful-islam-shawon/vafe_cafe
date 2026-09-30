/** @odoo-module **/

import { Chatter } from "@mail/chatter/web_portal/chatter";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

// Contact chatter integration for sending a single SMS through the ADN gateway.
patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.contactSmsAction = useService("action");
    },

    async onClickContactSms() {
        await this.contactSmsAction.doAction(
            "contact_adn_sms.action_contact_sms_builder",
            {
                additionalContext: {
                    active_id: this.props.threadId,
                    active_ids: [this.props.threadId],
                    active_model: "res.partner",
                    default_partner_id: this.props.threadId,
                    default_contact_id: this.props.threadId,
                },
                onClose: () => {
                    this.load(this.state.thread, ["messages"]);
                },
            }
        );
    },
});
