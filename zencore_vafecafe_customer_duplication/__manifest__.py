{
    "name": "Zencore Vafecafe Customer Duplication",
    "version": "18.0.1.3.1",
    "category": "Website/eCommerce",
    "summary": "Prevent and merge duplicate customers by phone number",
    "author": "Saiful islam shawon",
    "license": "LGPL-3",

    "depends": [
        "website_sale",
        "meta_otp_auth",
    ],

    "data": [
        "security/ir.model.access.csv",
        "data/server_action.xml",
        "data/contact_merge_cron.xml",
        "views/server_action_views.xml",
    ],

    "installable": True,
    "application": False,
}
