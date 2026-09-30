{
    "name": "Contact ADN SMS",
    "summary": "Send a single ADN SMS from the Contact chatter",
    "version": "18.0.1.0.0",
    "category": "Productivity/Discuss",
    "license": "LGPL-3",
    "depends": ["contacts", "mail", "betopia_sms", 'sms',],
    "data": [
        "wizard/sms_builder_views.xml",
        "views/contact_sms_actions.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "contact_adn_sms/static/src/chatter/contact_sms_button.js",
            "contact_adn_sms/static/src/chatter/contact_sms_button.xml",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}

