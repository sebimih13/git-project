import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from checks import AbstractCheck
from trace2 import Trace2, trace2


class Trace2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / 'events.jsonl'
        self.env = mock.patch.dict(os.environ, GIT_TRACE2_EVENT=str(self.target),
                                   GIT_TRACE2_PARENT_SID='git-parent')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.tracer = Trace2(hook='pre-commit', branch='refs/heads/main')
        self.addCleanup(lambda: Trace2._instance and Trace2._instance.finish())

    def events(self):
        return [json.loads(line) for line in self.target.read_text().splitlines()]

    def test_singleton_finish_and_new_session(self):
        self.assertIs(Trace2(), self.tracer)
        sid = self.tracer.sid
        self.tracer.finish(1)
        self.tracer.finish(0)
        self.assertEqual(os.environ['GIT_TRACE2_PARENT_SID'], 'git-parent')
        self.assertEqual(sum(e['event'] == 'atexit' for e in self.events()), 1)
        next_tracer = Trace2()
        self.assertIsNot(next_tracer, self.tracer)
        self.assertNotEqual(next_tracer.sid, sid)
        self.assertEqual(next_tracer.parent_sid, 'git-parent')

    def test_decorator_preserves_return_metadata_and_exception(self):
        @trace2(name='quoted " check')
        def check(value):
            """Check documentation."""
            return value

        error = ValueError('private exception text')

        @trace2
        def failing():
            raise error

        value = object()
        self.assertIs(check(value), value)
        self.assertEqual(check.__name__, 'check')
        self.assertEqual(check.__doc__, 'Check documentation.')
        with self.assertRaises(ValueError) as caught:
            failing()
        self.assertIs(caught.exception, error)
        self.tracer.finish(1)
        events = self.events()
        self.assertEqual([e['value']['outcome'] for e in events if e['event'] == 'data_json'],
                         ['success', 'error'])
        self.assertNotIn('private exception text', self.target.read_text())
        hint = json.loads(next(e for e in events if e['event'] == 'region_enter')['msg'])
        self.assertEqual(hint['hook'], 'pre-commit')
        self.assertEqual(hint['branch'], 'refs/heads/main')

    def test_inherited_run_and_nested_helper(self):
        @trace2(name='helper')
        def helper():
            return 42

        class MyCheck(AbstractCheck):
            def execute(self):
                return helper()

        self.assertEqual(MyCheck().run(), 42)
        self.tracer.finish()
        enters = [e for e in self.events() if e['event'] == 'region_enter']
        leaves = [e for e in self.events() if e['event'] == 'region_leave']
        self.assertEqual([e['label'] for e in enters], ['MyCheck', 'helper'])
        self.assertEqual([e['nesting'] for e in enters], [1, 2])
        self.assertEqual([e['label'] for e in leaves], ['helper', 'MyCheck'])
        self.assertEqual(enters[1]['msg'], '')

    def test_entry_is_emitted_before_body_and_cancellation_balances(self):
        @trace2(name='cancelled')
        def check():
            self.assertEqual(self.events()[-1]['event'], 'region_enter')
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            check()
        self.tracer.finish(1)
        events = self.events()
        self.assertEqual(events[-3]['event'], 'region_leave')
        self.assertEqual(events[-2]['code'], 1)
        self.assertEqual(next(e['value']['outcome'] for e in events
                              if e['event'] == 'data_json'), 'error')

    def test_async_and_generator_decorators_rejected(self):
        async def asynchronous():
            pass

        def generator():
            yield 1

        for function in (asynchronous, generator):
            with self.assertRaises(TypeError):
                trace2(function)

    def test_worker_thread_rejected_without_corrupting_regions(self):
        errors = []

        def worker():
            try:
                with self.tracer.check('worker'):
                    pass
            except RuntimeError as error:
                errors.append(error)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.tracer._depth, 0)

    def test_write_failure_preserves_function_result(self):
        self.tracer.file.close()
        sink = mock.Mock()
        sink.write.side_effect = OSError('broken sink')
        self.tracer.file = sink

        @trace2
        def check():
            return 42

        self.assertEqual(check(), 42)
        self.assertIsNone(self.tracer.file)
        self.assertEqual(self.tracer._depth, 0)
