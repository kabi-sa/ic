-- Create the sign-in accounts on the hosted database.
-- Safe to run more than once: e-mails that already exist are left untouched.
-- Touches only the ic_events schema; HC HUB's public schema is never referenced.

insert into ic_events.users
  (name, email, password_hash, role, department, job_title,
   passwordless, approver_level, is_final_approver, active, created_at)
values
  ('Internal Communication', 'ic@kabi.ai', 'pbkdf2$ed3a6aee95bc544a10d7c3b250edcd8e$d949b47ee9a1ed068d586d7b267ac3269292a14d0d90e3500a67f793273c5e65', 'admin', 'Internal Communication', 'IC Administrator', 0, null, 0, 1, '2026-08-18 11:12:46'),
  ('Njood Aloraij', 'naloraij@kabi.ai', 'pbkdf2$bb26727fd3bf9c4e82bdd8c649d12712$614165cd4b06da0d59de2e799bfe00d61dddf21761e615fcecb0481f197bf931', 'ic_user', 'Internal Communication', 'Internal Communication Specialist', 1, null, 0, 1, '2026-08-18 11:12:46'),
  ('Mohammed Al-Otaibi', 'm.manager@kabi.ai', 'pbkdf2$705ba743223e592346100bfa97fe60b8$70d6b829910b0fe3681dec771e1c2814d7d7f9aeff67c6b97a6c4cee0ab43926', 'manager', 'Human Capital', 'Human Capital Director', 1, 1, 1, 1, '2026-08-18 11:12:46'),
  ('Mashael', 'malkadi@kabi.ai', 'pbkdf2$1d9cadeee66dd97339c02c1ac5b18216$ac3807464a64668dd7d641a15101d9544ee1773c6ca0ad36371f3f00267775d1', 'manager', null, 'Invited approver', 1, 1, 0, 1, '2026-08-18 11:12:46')
on conflict (email) do nothing;

-- Point emailed approval links at the deployment instead of localhost.
update ic_events.settings
   set value = 'https://ic-beta.vercel.app'
 where key = 'app_base_url';

-- Confirm the result.
select (select count(*) from ic_events.users)                       as accounts,
       (select count(*) from ic_events.users where role = 'admin')   as admins,
       (select value from ic_events.settings where key = 'app_base_url') as app_base_url;
