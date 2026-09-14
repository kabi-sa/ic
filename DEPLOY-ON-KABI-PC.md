# Hosting the IC Events Approval Hub on a KABi PC

No GitHub, no Vercel, no external accounts. The hub runs on one machine inside KABi and
everyone else opens it in a browser. All the data — events, pictures, announcements,
accounts — lives in two folders on that machine.

## What you need

* A Windows PC that stays on during working hours. A desktop is better than a laptop:
  a laptop that sleeps or goes home takes the hub with it.
* Python 3.10 or newer, with **Add python.exe to PATH** ticked during installation.
* This whole `ic-events-hub` folder, copied to the machine. Somewhere plain like
  `C:\KABi\ic-events-hub` is easier to live with than a path inside OneDrive, which
  syncs constantly and can lock the database file.

## Setting it up (once)

1. **Open the port.** Right-click `OPEN-FIREWALL.bat` → *Run as administrator*.
   Windows blocks incoming connections by default, so without this nobody else can
   reach the hub. It opens the port on the office and private networks only, never on
   public Wi-Fi.

2. **Set the address for e-mails.** Double-click `SET-TEAM-ADDRESS.bat`.
   Approval e-mails carry a link back into the hub; that link has to use this PC's
   network address or it will not open on anyone else's machine. The script works the
   address out by itself and shows you what it chose.

3. **Start it automatically.** Right-click `INSTALL-AUTOSTART.bat` → *Run as
   administrator*. The hub then comes back on its own after a restart, keeps running
   when nobody is signed in, and restarts if it ever stops.

   Prefer to keep it visible instead? Skip this and use `HOST-ON-THIS-PC.bat`, which
   runs the hub in a window you can watch. That window has to stay open.

4. **Check it from another PC.** On a colleague's machine, open the address the
   second step printed — something like `http://192.168.100.187:8080`. If it loads,
   you are done.

## Telling the team

> The IC Events Approval Hub is at **http://192.168.100.187:8080**
> Sign in with your **name and work e-mail** — no password.
> Only people the IC team has added can get in.

Approvers do not need to sign in at all: the e-mail they receive carries a personal
link straight to the approval page.

Ask IT for a friendly name if you would rather not hand out an IP address. They can
point something like `ic-hub.kabi.local` at the machine, and then you re-run
`SET-TEAM-ADDRESS.bat http://ic-hub.kabi.local:8080`.

## Backups — please read this

Everything lives in exactly two places:

    data\ic_hub.db      every event, account, decision and setting
    uploads\            every picture, announcement and quotation file

`BACKUP-NOW.bat` copies both into `backups\<date>-<time>\`. It uses SQLite's own backup
command, so it is safe to run while people are using the hub.

Two things worth being blunt about:

* **A backup on the same PC is not a backup.** Copy the folder to OneDrive, a network
  share, or a USB drive. If that machine's disk fails, everything goes with it.
* **Nothing does this for you.** With the database on this PC there is no provider
  keeping snapshots. Put a reminder in your calendar — monthly is enough for this
  amount of activity, weekly if a busy season is running.

To restore: put `ic_hub.db` back into `data\` and the `uploads` folder back beside it.
That is the whole procedure.

## Moving it to a different PC

1. Run `BACKUP-NOW.bat` on the old machine.
2. Copy the `ic-events-hub` folder to the new one, including `data\` and `uploads\`.
3. Install Python, then follow *Setting it up* above.
4. On the old machine, right-click `UNINSTALL-AUTOSTART.bat` → *Run as administrator*,
   so two copies are not running with two different sets of data.

## Everyday commands

| To do this | Run this |
|---|---|
| Start it now, without rebooting | `schtasks /Run /TN "KABi IC Events Hub"` |
| Stop it | `schtasks /End /TN "KABi IC Events Hub"` |
| See whether it is running | `schtasks /Query /TN "KABi IC Events Hub"` |
| Run it in a visible window | `HOST-ON-THIS-PC.bat` |
| Use a different port | `HOST-ON-THIS-PC.bat 9000` |

## If something is wrong

**Nobody else can reach it.** Run `OPEN-FIREWALL.bat` as administrator. If it still
fails, KABi's network may separate wireless from wired clients — ask IT whether the two
can see each other.

**It works by name but not by address, or vice versa.** Re-run
`SET-TEAM-ADDRESS.bat` so e-mailed links match what people actually type.

**Approval links in e-mails do not open.** The stored address is wrong or has changed —
this happens when the PC's IP is handed out by DHCP and shifts. Ask IT to reserve a
fixed address for the machine, then re-run `SET-TEAM-ADDRESS.bat`.

**The page loads but pictures do not.** Check that `uploads\` came across with the
database. The two belong together.

**Port already in use.** Something else has 8080. Use another port —
`HOST-ON-THIS-PC.bat 9000` — and re-run `SET-TEAM-ADDRESS.bat` and `OPEN-FIREWALL.bat 9000`.

## A note on security

The hub is deliberately reachable only from inside KABi's network. It speaks plain HTTP,
which is fine on an internal network and not fine on the open internet — do not forward
a router port to it. If people need access from outside the office, that should go
through KABi's VPN, or be raised with IT as a proper hosted deployment with a
certificate.

Sign-in is by name and e-mail for everyone except the IC administrator, who has a
password. That is a deliberate trade for an internal tool: it keeps approvers out of
password resets. It also means anyone who can reach the hub and knows a colleague's
work e-mail can sign in as them — which is acceptable inside the office network, and
another reason not to expose it publicly.

## Files you can ignore now

`vercel.json`, the `api\` folder, `PUSH-TO-GITHUB.bat`, `SAVE-CONNECTION.bat`,
`VERIFY-SUPABASE.bat` and `supabase\` were for the cloud deployment. Nothing uses them
when the hub runs like this. They are harmless, and worth keeping in case KABi ever
wants a hosted copy again.
