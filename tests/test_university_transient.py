import tempfile, unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from together.crawler.university import NoticeUpdater, UniversityResponseDeferred


class UniversityTransient(unittest.TestCase):
    def test_500_retries_same_url_before_reading_saved_anchor(self):
        failed=Mock(status_code=500)
        ok=Mock(status_code=200,text='<table><tr><td class="num">17</td><td class="subject"><a>saved</a></td></tr></table>')
        updater=NoticeUpdater()
        with patch('together.crawler.university.requests.get',side_effect=[failed,ok]) as get, patch('together.crawler.university.time.sleep'):
            self.assertEqual(updater.fetch_notice_number('saved'),'17')
        self.assertEqual(get.call_args_list[0].args,get.call_args_list[1].args)
        failed.close.assert_called_once()

    def test_persistent_error_does_not_write_csv_or_yesterday(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);y=root/'yesterday.txt';c=root/'pages.csv'
            y.write_text('saved\npinned\n');c.write_text('original CSV bytes\n')
            u=NoticeUpdater(str(y),str(c))
            with patch('together.crawler.university.time.monotonic',side_effect=[0,0,59.95]),patch('together.crawler.university.requests.get',return_value=Mock(status_code=500)),patch('together.crawler.university.time.sleep'):
                with self.assertRaises(UniversityResponseDeferred):u.update_university_notices()
            self.assertEqual(y.read_text(),'saved\npinned\n');self.assertEqual(c.read_text(),'original CSV bytes\n')

    def test_non_transient_http_error_still_requires_investigation(self):
        r=Mock(status_code=403);r.raise_for_status.side_effect=requests.HTTPError('forbidden')
        with patch('together.crawler.university.requests.get',return_value=r):
            with self.assertRaises(requests.HTTPError):NoticeUpdater().fetch_notice_number('saved')

    def test_application_defers_only_transient_university_preparation(self):
        from together.application import prepare_university
        u=Mock();u.update_university_notices.side_effect=UniversityResponseDeferred()
        with patch('together.application.event') as event:self.assertFalse(prepare_university(u))
        self.assertEqual(event.call_args.kwargs['outcome'],'response_deferred')
        u.update_university_notices.side_effect=PermissionError()
        with self.assertRaises(PermissionError):prepare_university(u)
