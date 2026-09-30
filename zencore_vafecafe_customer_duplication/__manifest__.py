{
    "name": "Zencore Vafecafe Customer Duplication",
    "version": "18.0.1.1.0",
    "category": "Website/eCommerce",
    "summary": "Prevent duplicate guest customers, signup contacts and delivery addresses",
    "author": "Saiful islam shawon",
    "license": "LGPL-3",

    "depends": [
        "website_sale",
        "auth_signup",
        "meta_otp_auth",
    ],

    "data": [
        "views/auth_signup_templates.xml",
    ],

    "installable": True,
    "application": False,
}
