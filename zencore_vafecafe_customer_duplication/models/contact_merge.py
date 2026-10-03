# -*- coding: utf-8 -*-

import logging
import re
from collections import defaultdict

from odoo import api, fields, models, _, Command
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class IrActionsServer(models.Model):
    _inherit = 'ir.actions.server'

    is_zencore_duplicate_merge_action = fields.Boolean(
        compute='_compute_is_zencore_duplicate_merge_action',
    )

    def _compute_is_zencore_duplicate_merge_action(self):
        merge_action = self.env.ref(
            'zencore_vafecafe_customer_duplication.action_merge_duplicate_contacts',
            raise_if_not_found=False,
        )
        for action in self:
            action.is_zencore_duplicate_merge_action = bool(
                merge_action and action.id == merge_action.id
            )

    def action_open_zencore_duplicate_merge_wizard(self):
        self.ensure_one()
        expected_action = self.env.ref(
            'zencore_vafecafe_customer_duplication.action_merge_duplicate_contacts',
            raise_if_not_found=False,
        )
        if not expected_action or self.id != expected_action.id:
            raise UserError(_('This operation is only available on the Duplicate Contact Merge action.'))

        wizard = self.env['zencore.contact.merge.wizard'].create({})
        return {
            'type': 'ir.actions.act_window',
            'name': _('Merge Duplicate Contacts'),
            'res_model': 'zencore.contact.merge.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }


class ZencoreContactMergeMixin(models.AbstractModel):
    _name = 'zencore.contact.merge.mixin'
    _description = 'Duplicate Contact Merge Helpers'

    @api.model
    def _normalize_phone(self, phone):
        if not phone:
            return False
        digits = re.sub(r'\D', '', str(phone))
        if digits.startswith('880') and len(digits) == 13:
            digits = '0' + digits[3:]
        elif len(digits) == 10 and digits.startswith('1'):
            digits = '0' + digits
        if not (
            len(digits) == 11
            and digits.startswith('01')
            and digits[2:3] in tuple('3456789')
        ):
            return False
        return digits

    @api.model
    def _get_duplicate_groups(self):
        Partner = self.env['res.partner'].sudo().with_context(active_test=False)
        partners = Partner.search([
            ('parent_id', '=', False),
            '|', ('phone', '!=', False), ('mobile', '!=', False),
        ])
        grouped = defaultdict(lambda: Partner.browse())
        for partner in partners:
            normalized_values = {
                value for value in (
                    self._normalize_phone(partner.phone),
                    self._normalize_phone(partner.mobile),
                ) if value
            }
            for normalized in normalized_values:
                grouped[normalized] |= partner

        result = []
        already_grouped = set()
        epoch = fields.Datetime.from_string('1970-01-01 00:00:00')
        for normalized, group in grouped.items():
            group = group.filtered(lambda p: p.id not in already_grouped)
            if len(group) < 2:
                continue
            ordered = group.sorted(key=lambda p: (p.create_date or epoch, p.id))
            result.append((normalized, ordered))
            already_grouped.update(ordered.ids)
        return result

    @api.model
    def _prepare_users_for_merge(self, master, partners):
        Users = self.env['res.users'].sudo().with_context(active_test=False)
        users = Users.search([('partner_id', 'in', partners.ids)], order='id asc')
        if not users:
            return

        master_users = users.filtered(lambda u: u.partner_id.id == master.id)
        if master_users:
            keeper = master_users.sorted('id')[0]
        else:
            partner_rank = {partner.id: index for index, partner in enumerate(partners)}
            keeper = users.sorted(lambda u: (partner_rank.get(u.partner_id.id, 999999), u.id))[0]
            keeper.write({'partner_id': master.id})

        users_to_remove = users - keeper
        if self.env.user in users_to_remove:
            raise UserError(_(
                'The currently logged-in user belongs to a duplicate contact that would be removed. '
                'This group was skipped for safety.'
            ))
        if users_to_remove:
            users_to_remove.unlink()

    @api.model
    def _prepare_website_visitors_for_merge(self, partners):
        """Consolidate visitors before Odoo's standard partner merge.

        Odoo 18's website extension of the partner merge wizard correctly
        merges source visitors when the destination partner already has a
        visitor.  When the destination has no visitor but multiple source
        partners do, however, the generic foreign-key update can move more
        than one visitor to the destination.  The website post-processing
        then tries to synchronize every visitor access_token to the same
        destination partner id, which violates the unique access-token
        constraint.

        Use website.visitor's own _merge_visitor() helper so its related
        tracking data is consolidated by Odoo before the partner merge.
        """
        if 'website.visitor' not in self.env.registry.models:
            return

        partners = partners.exists().sudo().with_context(active_test=False)
        visitors = self.env['website.visitor'].sudo().search(
            [('partner_id', 'in', partners.ids)],
            order='id asc',
        )
        if len(visitors) < 2:
            return

        # Prefer a visitor already linked to the oldest/master partner.  If
        # the master has none, keep the oldest visitor in the duplicate group.
        master = partners[0]
        master_visitors = visitors.filtered(lambda visitor: visitor.partner_id.id == master.id)
        keeper = master_visitors[:1] or visitors[:1]

        for visitor in visitors - keeper:
            visitor._merge_visitor(keeper)

    @api.model
    def _merge_partner_group(self, partners):
        partners = partners.exists().sudo().with_context(active_test=False)
        if len(partners) < 2:
            return 0
        epoch = fields.Datetime.from_string('1970-01-01 00:00:00')
        partners = partners.sorted(key=lambda p: (p.create_date or epoch, p.id))
        master = partners[0]
        sources = partners - master
        self._prepare_users_for_merge(master, partners)
        self._prepare_website_visitors_for_merge(partners)

        merge_model = self.env['base.partner.merge.automatic.wizard'].sudo()
        merged = 0
        while sources:
            batch_sources = sources[:2]
            merge_model._merge((master | batch_sources).ids, dst_partner=master, extra_checks=False)
            merged += len(batch_sources)
            sources -= batch_sources
        return merged


class ZencoreContactMergeWizard(models.TransientModel):
    _name = 'zencore.contact.merge.wizard'
    _inherit = 'zencore.contact.merge.mixin'
    _description = 'Merge Duplicate Contacts by Phone'

    duplicate_group_count = fields.Integer(string='Duplicate Groups', compute='_compute_duplicate_stats')
    duplicate_contact_count = fields.Integer(string='Duplicate Contacts to Merge', compute='_compute_duplicate_stats')

    @api.depends()
    def _compute_duplicate_stats(self):
        groups = self._get_duplicate_groups()
        group_count = len(groups)
        contact_count = sum(len(partners) - 1 for _phone, partners in groups)
        for wizard in self:
            wizard.duplicate_group_count = group_count
            wizard.duplicate_contact_count = contact_count

    def action_merge_all_duplicates(self):
        self.ensure_one()
        if not self.env.user.has_group('base.group_system'):
            raise UserError(_('Only an Administrator can merge all duplicate contacts.'))

        running_job = self.env['zencore.contact.merge.job'].sudo().search([
            ('state', 'in', ['pending', 'running'])
        ], limit=1)
        if running_job:
            raise UserError(_(
                'A duplicate contact merge job is already pending or running. '
                'Please let it finish before starting another one.'
            ))

        groups = self._get_duplicate_groups()
        if not groups:
            return {
                'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {
                    'title': _('Duplicate Contact Merge'),
                    'message': _('No duplicate main contacts were found by phone number.'),
                    'type': 'info', 'sticky': False,
                },
            }

        job = self.env['zencore.contact.merge.job'].sudo().create({
            'requested_by': self.env.user.id,
            'total_groups': len(groups),
            'total_contacts': sum(len(partners) - 1 for _phone, partners in groups),
        })
        self.env['zencore.contact.merge.job.line'].sudo().create([
            {
                'job_id': job.id,
                'phone': phone,
                'partner_ids': [Command.set(partners.ids)],
            }
            for phone, partners in groups
        ])

        cron = self.env.ref(
            'zencore_vafecafe_customer_duplication.ir_cron_process_contact_merge',
            raise_if_not_found=False,
        )
        if not cron:
            raise UserError(_('The background merge scheduled action is missing. Please upgrade the module.'))
        cron.sudo()._trigger()

        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _('Duplicate Contact Merge Started'),
                'message': _(
                    'The merge has been queued in safe background batches: %(groups)s groups / %(contacts)s duplicate contacts.',
                    groups=job.total_groups, contacts=job.total_contacts,
                ),
                'type': 'success', 'sticky': True,
            },
        }


class ZencoreContactMergeJob(models.Model):
    _name = 'zencore.contact.merge.job'
    _inherit = 'zencore.contact.merge.mixin'
    _description = 'Duplicate Contact Merge Job'
    _order = 'id desc'

    state = fields.Selection([
        ('pending', 'Pending'), ('running', 'Running'),
        ('done', 'Done'), ('done_with_errors', 'Done with Errors'),
    ], default='pending', required=True, index=True)
    requested_by = fields.Many2one('res.users', readonly=True)
    total_groups = fields.Integer(readonly=True)
    total_contacts = fields.Integer(readonly=True)
    processed_groups = fields.Integer(readonly=True)
    merged_contacts = fields.Integer(readonly=True)
    failed_groups = fields.Integer(readonly=True)
    line_ids = fields.One2many('zencore.contact.merge.job.line', 'job_id')

    @api.model
    def _cron_process_contact_merge(self):
        # Keep each callback deliberately small. Odoo's cron progress API will
        # immediately schedule more callbacks while work remains.
        job = self.sudo().search([('state', 'in', ['pending', 'running'])], order='id asc', limit=1)
        if not job:
            self.env['ir.cron']._notify_progress(done=0, remaining=0)
            return

        if job.state == 'pending':
            job.state = 'running'

        lines = self.env['zencore.contact.merge.job.line'].sudo().search([
            ('job_id', '=', job.id), ('state', '=', 'pending')
        ], order='id asc', limit=3)

        done_now = 0
        for line in lines:
            try:
                with self.env.cr.savepoint():
                    partners = line.partner_ids.exists().sudo().with_context(active_test=False)
                    merged = self._merge_partner_group(partners)
                    line.write({'state': 'done', 'merged_contacts': merged})
                job.write({
                    'processed_groups': job.processed_groups + 1,
                    'merged_contacts': job.merged_contacts + merged,
                })
            except Exception as exc:  # one bad group must not stop all other groups
                _logger.exception('Could not merge duplicate phone group %s', line.phone)
                line.write({'state': 'failed', 'error_message': str(exc)[:2000]})
                job.write({
                    'processed_groups': job.processed_groups + 1,
                    'failed_groups': job.failed_groups + 1,
                })
            done_now += 1

        remaining = self.env['zencore.contact.merge.job.line'].sudo().search_count([
            ('job_id', '=', job.id), ('state', '=', 'pending')
        ])
        if not remaining:
            job.state = 'done_with_errors' if job.failed_groups else 'done'
            _logger.info(
                'Duplicate contact merge job %s completed: %s contacts merged, %s groups failed',
                job.id, job.merged_contacts, job.failed_groups,
            )

        self.env['ir.cron']._notify_progress(done=done_now, remaining=remaining)


class ZencoreContactMergeJobLine(models.Model):
    _name = 'zencore.contact.merge.job.line'
    _description = 'Duplicate Contact Merge Job Line'
    _order = 'id'

    job_id = fields.Many2one('zencore.contact.merge.job', required=True, ondelete='cascade', index=True)
    phone = fields.Char(required=True, index=True)
    partner_ids = fields.Many2many('res.partner', string='Contacts')
    state = fields.Selection([
        ('pending', 'Pending'), ('done', 'Done'), ('failed', 'Failed')
    ], default='pending', required=True, index=True)
    merged_contacts = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)
