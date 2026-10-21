"""Reuse a local ChromeDriver session without issuing New Session."""
import json
from urllib.request import urlopen
from urllib.error import URLError
from urllib.parse import urlparse
from selenium import webdriver

def attach(port):
    if not isinstance(port,int) or not 1<=port<=65535:raise ValueError('Invalid local port')
    endpoint=f'http://127.0.0.1:{port}'
    try:
        with urlopen(endpoint+'/sessions',timeout=5) as response: sessions=json.load(response).get('value',[])
    except (URLError,TimeoutError,OSError) as exc:
        raise ValueError(f'Local ChromeDriver port {port} is unavailable. Keep the Python process that owns the browser open when attaching. For an explicitly new standalone run, omit --attach-port. No browser was created and no TikTok request was sent.') from None
    if len(sessions)!=1:raise ValueError('Expected exactly one existing ChromeDriver session')
    session=sessions[0];session_id=session['id']
    class ExistingDriver(webdriver.Remote):
        def start_session(self,capabilities):
            self.session_id=session_id;self._caps=session.get('capabilities',{})
        def execute_cdp_cmd(self,cmd,cmd_args):
            return self.execute('executeCdpCommand',{'cmd':cmd,'params':cmd_args})['value']
        def get_log(self,log_type):
            return self.execute('getLog',{'type':log_type})['value']
    driver=ExistingDriver(command_executor=endpoint,options=webdriver.ChromeOptions())
    driver.command_executor._commands['executeCdpCommand']=('POST','/session/$sessionId/goog/cdp/execute')
    driver.command_executor._commands['getLog']=('POST','/session/$sessionId/se/log')
    url=urlparse(driver.current_url)
    if url.hostname!='www.tiktok.com':raise ValueError('Existing session is not on TikTok; refusing navigation')
    return driver
