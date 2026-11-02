"""Manual Foody sign-in / local masked cookie input. No crawling or credential logs."""
import argparse
import os
from pathlib import Path


def cookie_payload(value, name, domain, path, expiry='', same_site='', secure=True, http_only=True):
    value, name, domain, path = (x.strip() for x in (value, name, domain, path))
    host = domain.lstrip('.').lower()
    if not value or len(value) > 16384 or any(ord(x) < 33 for x in value):
        raise ValueError('Cookie value is empty or malformed')
    if not name or any(x in name for x in ' ;=\r\n\t'):
        raise ValueError('Invalid cookie name')
    if host != 'foody.vn' and not host.endswith('.foody.vn'):
        raise ValueError('Domain must be copied from a Foody cookie, within foody.vn')
    if any(x in domain for x in '/:@ \r\n\t') or not path.startswith('/'):
        raise ValueError('Copy the exact domain and path from the original browser')
    result = dict(name=name, value=value, domain=domain, path=path,
                  secure=secure, httpOnly=http_only)
    if expiry.strip():
        expires = float(expiry)
        if not 0 < expires < 253402300800:
            raise ValueError('Expiry must be an actual Unix timestamp, or blank for a session cookie')
        result['expires'] = expires
    if same_site:
        if same_site not in ('Strict', 'Lax', 'None'):
            raise ValueError('Invalid SameSite value')
        result['sameSite'] = same_site
    return result


def prompt_cookie():
    import tkinter as tk
    from tkinter import simpledialog, messagebox, ttk
    class CookieDialog(simpledialog.Dialog):
        def body(self, master):
            tk.Label(master, text='Copy the exact cookie attributes from the original browser.\n'
                     'Input stays local. Leave expiry blank only for a session cookie.').grid(
                         row=0, column=0, columnspan=2, pady=8)
            self.inputs = {}
            fields = [('value', 'Cookie value (masked)', ''), ('name', 'Name', 'ID_FOODY.AUTH'),
                      ('domain', 'Domain (required; do not guess)', ''), ('path', 'Path', '/'),
                      ('expiry', 'Expires (Unix seconds; blank = session)', '')]
            for row, (key, label, default) in enumerate(fields, 1):
                tk.Label(master, text=label).grid(row=row, column=0, sticky='w')
                entry = tk.Entry(master, width=55, show='*' if key == 'value' else '')
                entry.insert(0, default); entry.grid(row=row, column=1, pady=3)
                self.inputs[key] = entry
            self.secure = tk.BooleanVar(value=True); self.http_only = tk.BooleanVar(value=True)
            tk.Checkbutton(master, text='Secure', variable=self.secure).grid(row=6, column=0)
            tk.Checkbutton(master, text='HttpOnly', variable=self.http_only).grid(row=6, column=1)
            tk.Label(master, text='SameSite (match original)').grid(row=7, column=0)
            self.same_site = ttk.Combobox(master, values=['', 'Strict', 'Lax', 'None'], state='readonly')
            self.same_site.set(''); self.same_site.grid(row=7, column=1)
            return self.inputs['value']
        def validate(self):
            try:
                self.result = cookie_payload(**{k: v.get() for k,v in self.inputs.items()},
                    same_site=self.same_site.get(), secure=self.secure.get(), http_only=self.http_only.get())
                return True
            except (ValueError, OverflowError):
                messagebox.showerror('Check cookie attributes',
                    'Check value/name, exact Foody domain/path and expiry. No values were logged.', parent=self)
                return False
        def apply(self):
            for entry in self.inputs.values():
                entry.delete(0, tk.END)
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        return CookieDialog(root, title='Foody private session — exact attributes').result
    finally:
        root.destroy()


def import_cookie(context, value):
    # Attributes come from local operator input, not guessed domain/expiry values.
    context.add_cookies([value])


def profile_directory():
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        raise ValueError('LOCALAPPDATA is required for a private profile outside Git')
    return Path(local) / 'FnbFoodyResearch' / 'chrome-profile'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cookie-input', action='store_true',
                        help='Prompt locally using a masked dialog; never pass the cookie on the command line')
    args = parser.parse_args()
    from playwright.sync_api import sync_playwright
    value = prompt_cookie() if args.cookie_input else None
    if args.cookie_input and value is None:
        print('LOCAL_INPUT_CANCELLED', flush=True)
        return
    folder = profile_directory()
    folder.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(folder), channel='chrome',
            headless=False, viewport={'width':1280, 'height':900})
        try:
            if value is not None:
                try:
                    import_cookie(context, value)
                finally:
                    # Drop the input reference; Python cannot guarantee secure RAM erasure.
                    value = None
                print('LOCAL_SESSION_IMPORTED; account/access validity not yet verified.', flush=True)
            page = context.pages[0] if context.pages else context.new_page()
            print('MANUAL_LOGIN_WINDOW_OPEN; close this Chrome window after signing in.', flush=True)
            try:
                page.goto('https://www.foody.vn/ho-chi-minh/dia-diem-tai-quan-4?categorygroup=food',
                          wait_until='domcontentloaded', timeout=30000)
            except Exception:
                print('Navigation not confirmed; use the visible browser manually.', flush=True)
            # User controls sign-in and closing. No credential inspection, screenshots,
            # cookie exports, page-content logs or automatic challenge interaction.
            context.wait_for_event('close', timeout=0)
            print('MANUAL_WINDOW_CLOSED; session validity still requires access verification.', flush=True)
        finally:
            try:
                context.close()
            except Exception:
                pass


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('LOGIN_HELPER_STOPPED: ' + type(exc).__name__, flush=True)
        raise SystemExit(1)
