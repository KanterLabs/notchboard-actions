import io
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError
from sources import HTTP, Gitea


class SourceTests(unittest.TestCase):
    def test_conditional_get_and_304(self):
        client=HTTP('placeholder',True)
        response=io.StringIO('{"workflow_runs":[]}')
        response.headers={'ETag':'"example"'}
        opener=Mock()
        opener.open.side_effect=[response,HTTPError('https://api.github.com/example',304,'',{},None)]
        with patch('sources.build_opener',return_value=opener):
            first=client.get('https://api.github.com/example')
            self.assertEqual(first,client.get('https://api.github.com/example'))
        request=opener.open.call_args.args[0]
        self.assertEqual(request.get_method(),'GET')
        self.assertEqual(request.get_header('If-none-match'),'"example"')
        self.assertEqual(opener.open.call_args.kwargs['timeout'],10)

    def test_401_reloads_token(self):
        client=HTTP('placeholder',True)
        response=io.StringIO('{}'); response.headers={}
        opener=Mock()
        opener.open.side_effect=[HTTPError('https://api.github.com/example',401,'',{},None),response]
        with patch('sources.build_opener',return_value=opener), patch('sources.gh_token',return_value='replacement') as token:
            self.assertEqual(client.get('https://api.github.com/example'),{})
            token.assert_called_once()

    def test_gitea_deduplicates(self):
        client=Gitea.__new__(Gitea)
        client.cfg={'orgs':['my-org']}; client.url='https://gitea.example.com'
        client.http=Mock(last_lookup=float('-inf'));  client.http.token='placeholder'
        client.http.get.return_value={'workflow_runs':[{'id':1,'status':'running'}]}
        self.assertEqual(len(client.poll()),1)
        self.assertEqual(client.http.get.call_count,2)
