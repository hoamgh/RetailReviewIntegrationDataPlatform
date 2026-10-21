"""Reviewed fake driver for C2b child. Never imports/starts a browser or network."""
from pathlib import Path
import os
import subprocess
import sys
import time
import uuid


class FakeElement:
    text='fixture text';tag_name='div';size={'width':20,'height':20}
    def __init__(self,driver):self.driver=driver
    def click(self):self.driver.calls.append('click')
    def is_displayed(self):return True
    def find_element(self,*args):return FakeElement(self.driver)
    def find_elements(self,*args):return [FakeElement(self.driver)]
    def get_attribute(self,name):return 'synthetic'


class FakeDriver:
    def __init__(self,config):
        self.config=config;self.calls=[];self.session_id=uuid.uuid4().hex
        self.profile_dir=config['_profile_path']
        self.current_url='about:blank';self.window_handles=['fixture-tab'];self.current_window_handle='fixture-tab'
    def get(self,url):self.calls.append('get');self.current_url=url
    def set_page_load_timeout(self,value):pass
    def set_script_timeout(self,value):pass
    def get_log(self,kind):return self.config.get('performance_log',[])
    def execute_cdp_cmd(self,name,params):return {'body':'{}'}
    def find_elements(self,*args):return []
    def find_element(self,*args):return FakeElement(self)
    def save_screenshot(self,path):
        if self.config.get('crash_screenshot'):os._exit(81)
        Path(path).write_bytes(b'OFFLINE_FAKE_IMAGE');return True
    def execute_script(self,script,*args):
        self.calls.append('script')
        if script=='element':return FakeElement(self)
        if script=='nested':return {'items':[FakeElement(self)]}
        if script=='data-not-element':return {'type':'element','value':'forged'}
        if script=='calls':return list(self.calls)
        if script=='change-session':self.session_id='changed';return True
        if script=='change-profile':self.profile_dir='other-profile';return True
        if script=='unapproved-object':return object()
        if script=='crash-with-child':
            subprocess.Popen([sys.executable,'-B','-c','import threading; threading.Event().wait(20)'])
            os._exit(81)
        if script=='crash':os._exit(81)
        if script in ('wait','wait-descendant'):
            root=Path(self.config['control'])
            if script=='wait-descendant':subprocess.Popen([sys.executable,'-B','-c','import threading; threading.Event().wait(20)'])
            (root/'inflight').touch()
            deadline=time.monotonic()+6
            while not (root/'release').exists():
                if time.monotonic()>deadline:raise TimeoutError()
                time.sleep(.01)
            return True
        from experiments.tiktok_comment_dom.extractor import SNAPSHOT_JS
        from experiments.tiktok_access_benchmark.session_comments import STATE_JS
        if script=='return ('+SNAPSHOT_JS+')();':
            return {'url':self.current_url,'cards':[{'text':'synthetic review','created_at_raw':'2d ago'}],
                'comment_panel_present':True,'end_of_comments_observed':True}
        if script=='return ('+STATE_JS+')();':
            return self.config.get('access_state',{'video_detail':True,'comments':True,'dialog_evidence':[],'challenge':False})
        return True
