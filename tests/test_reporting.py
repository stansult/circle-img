"""Timestamp/progress contracts, real CLI streams, and unchanged saved pixels."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
import io
import os
from pathlib import Path
import subprocess
import select
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image
from test_edge_background import load, ROOT

app = load('reporting_app', ROOT/'circle-img.py')
SOURCE = ROOT/'tests/fixtures/layered-background.png'


class Clock:
    """Separate, explicitly controlled wall and monotonic clocks."""
    def __init__(self, wall=datetime(2026, 8, 31, 14, 7, 3), tick=100):
        self.wall = wall
        self.tick = tick

    def now(self):
        return self.wall

    def monotonic(self):
        return self.tick

    def advance(self, seconds, wall_seconds=None):
        self.wall += timedelta(seconds=seconds if wall_seconds is None else wall_seconds)
        self.tick += seconds


class Terminal(io.StringIO):
    """Thread-safe capture with bounded notification waits, not timing sleeps."""
    def __init__(self):
        super().__init__()
        self.changed = threading.Condition()
        self.flushes = 0

    def isatty(self):
        return True

    def write(self, text):
        with self.changed:
            result = super().write(text)
            self.changed.notify_all()
            return result

    def flush(self):
        with self.changed:
            self.flushes += 1
            self.changed.notify_all()

    def getvalue(self):
        with self.changed:
            return super().getvalue()

    def wait_for_text(self, text):
        deadline = time.monotonic()+3
        with self.changed:
            while text not in super().getvalue():
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f'progress did not emit {text!r}: {super().getvalue()!r}')
                self.changed.wait(remaining)


def reporter(clock=None, verbose=False, terminal=False):
    clock = clock or Clock()
    out, err = io.StringIO(), Terminal() if terminal else io.StringIO()
    with patch.dict(os.environ, {'TERM':'xterm'}):
        report = app.Reporter(verbose=verbose, stdout=out, stderr=err,
                              now=clock.now, monotonic=clock.monotonic)
    return report, out, err, clock


def terminal_run(command):
    """Drain a real PTY while its slave stays open (macOS drops unread data on close)."""
    import pty
    master,slave=pty.openpty()
    process=None
    chunks=[]
    try:
        with tempfile.TemporaryFile() as stdout:
            process=subprocess.Popen(command,stdout=stdout,stderr=slave,
                                     env={**os.environ,'TERM':'xterm'})
            deadline=time.monotonic()+15
            while True:
                if time.monotonic() >= deadline:
                    raise AssertionError('terminal subprocess timed out')
                ready,_,_=select.select([master],[],[],.05)
                if ready:
                    chunks.append(os.read(master,8192))
                elif process.poll() is not None:
                    break
            process.wait(timeout=1)
            stdout.seek(0)
            return process.returncode,stdout.read(),b''.join(chunks).decode()
    finally:
        if process is not None and process.poll() is None:
            process.kill(); process.wait(timeout=3)
        os.close(master); os.close(slave)


class ReportingTests(unittest.TestCase):
    def test_clock_and_terminal_fixture_contract(self):
        clock = Clock(datetime(2026,8,31,23,59,59),10)
        clock.advance(3,wall_seconds=-5)
        self.assertEqual(clock.now(),datetime(2026,8,31,23,59,54))
        self.assertEqual(clock.monotonic(),13)
        terminal=Terminal()
        self.assertTrue(terminal.isatty())
        worker=threading.Thread(target=lambda: (terminal.write('ready'), terminal.flush()))
        worker.start()
        terminal.wait_for_text('ready')
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(terminal.getvalue(),'ready')
        self.assertEqual(terminal.flushes,1)

    def test_timestamp_blocks_and_verbose_gating(self):
        report,out,err,clock=reporter()
        report.message('processing: input.png\n\ncontinuation')
        report.detail('hidden')
        clock.advance(8)
        report.message('Saved output.png')
        report.finish(); report.finish()
        self.assertEqual(out.getvalue(),'[2026-08-31]\n[14:07:03] processing: input.png\n\ncontinuation\n[14:07:11] Saved output.png\n')
        self.assertEqual(err.getvalue(),'')

    def test_midnight_date_follows_last_stream(self):
        report,out,err,clock=reporter(Clock(datetime(2026,8,31,23,59,59)))
        report.message('processing: input.png')
        clock.advance(3)
        report.message('error: failed\nexplanation',diagnostic=True)
        report.finish()
        self.assertEqual(out.getvalue(),'[2026-08-31]\n[23:59:59] processing: input.png\n')
        self.assertEqual(err.getvalue(),'[00:00:02] error: failed\nexplanation\n[2026-09-01]\n')

    def test_live_counter_has_fixed_start_time_and_new_completion_time(self):
        report,out,err,clock=reporter(terminal=True)
        with patch('shutil.get_terminal_size',return_value=os.terminal_size((100,24))):
            with report.stage('Removing background layer 2','Removed background layer 2'):
                self.assertIn('[14:07:03] Removing background layer 2... |',err.getvalue())
                self.assertNotIn('elapsed',err.getvalue())
                clock.advance(8)
                err.wait_for_text('[14:07:03] Removing background layer 2... elapsed 8s')
                self.assertNotIn('Removed background',err.getvalue())
                self.assertGreater(err.flushes,1)
            report.finish()
        text=err.getvalue()
        self.assertIn('[14:07:11] Removed background layer 2\n',text)
        self.assertNotIn('[14:07:11] Removing',text)
        self.assertEqual(text.count('[2026-08-31]'),1)
        self.assertEqual(out.getvalue(),'')
        self.assertFalse(report.line_visible)

    def test_live_elapsed_threshold(self):
        for duration,expected in ((0,None),(4.99,None),(5,'elapsed 5s'),(5.99,'elapsed 5s'),(8,'elapsed 8s')):
            with self.subTest(duration=duration):
                report,_,err,clock=reporter(terminal=True)
                started_at,started=clock.now(),clock.monotonic()
                clock.advance(duration)
                report._draw('Working',started_at,started,0)
                text=err.getvalue()
                self.assertIn('[14:07:03] Working...',text)
                if expected is None:
                    self.assertNotIn('elapsed',text)
                else:
                    self.assertIn(expected,text)
                report.finish()

    def test_completion_duration_threshold(self):
        for verbose in (False,True):
            for duration,expected in ((0,''),(9.94,''),(9.99,''),(10,' (took 10.0s)'),(14.2,' (took 14.2s)')):
                with self.subTest(verbose=verbose,duration=duration):
                    report,_,err,clock=reporter(verbose=verbose,terminal=not verbose)
                    with report.stage('Removing background','Removed background'):
                        clock.advance(duration)
                    prefix='verbose: ' if verbose else ''
                    self.assertTrue(err.getvalue().endswith(f'{prefix}Removed background{expected}\n'),err.getvalue())
                    report.finish()

    def test_monotonic_duration_when_wall_clock_moves_backward(self):
        report,_,err,clock=reporter(verbose=True)
        with report.stage('Loading','Loaded'):
            clock.advance(18,wall_seconds=-60)
        report.finish()
        self.assertIn('[14:06:03] verbose: Loaded (took 18.0s)',err.getvalue())
        self.assertNotIn('\r',err.getvalue())

    def test_failure_and_interrupt_clear_progress_and_join_worker(self):
        for exception,label in ((ValueError('bad'),'Failed'),(KeyboardInterrupt(),'Interrupted')):
            with self.subTest(label=label):
                report,_,err,clock=reporter(terminal=True)
                threads=set(threading.enumerate())
                with self.assertRaises(type(exception)):
                    with report.stage('Removing background layer 2','Removed background layer 2'):
                        clock.advance(8)
                        err.wait_for_text('elapsed 8s')
                        raise exception
                self.assertEqual(set(threading.enumerate()),threads)
                self.assertFalse(report.line_visible)
                self.assertIn(f'[14:07:11] {label}: Removing background layer 2\n',err.getvalue())
                self.assertNotIn('Removed background layer',err.getvalue())
                # A failed stage must not leave the next file stuck in animation.
                with report.stage('Loading next image','Loaded next image'):
                    pass
                report.finish()
                self.assertIn('Loaded next image',err.getvalue())

    def test_redirected_and_dumb_terminals_never_animate(self):
        for dumb in (False,True):
            with self.subTest(dumb=dumb):
                out,err=io.StringIO(),Terminal() if dumb else io.StringIO()
                with patch.dict(os.environ,{'TERM':'dumb' if dumb else 'xterm'}):
                    report=app.Reporter(stdout=out,stderr=err)
                with patch.object(app.threading,'Thread',side_effect=AssertionError('unexpected animation')):
                    with report.stage('Loading','Loaded'):
                        pass
                    report.finish()
                self.assertFalse(report.interactive)
                self.assertEqual(err.getvalue(),'')

    def test_long_stage_keeps_clocks_and_does_not_wrap(self):
        report,_,err,clock=reporter(terminal=True)
        with patch('shutil.get_terminal_size',return_value=os.terminal_size((60,24))):
            report._draw('Removing background '+'long '*40,clock.now(),clock.monotonic()-8,0)
        line=err.getvalue().split('\r\033[2K')[-1]
        self.assertLessEqual(len(line),59)
        self.assertTrue(line.startswith('[14:07:03] '))
        self.assertIn('elapsed 8s',line)
        report.finish()

    def test_stderr_width_is_used_and_tiny_terminals_skip_redraws(self):
        report,_,err,clock=reporter(terminal=True)
        with patch.object(err,'fileno',return_value=42), patch.object(app.os,'get_terminal_size',return_value=os.terminal_size((55,24))) as width:
            report._draw('Removing background layer 2',clock.now(),clock.monotonic()-8,0)
            width.assert_called_once_with(42)
        line=err.getvalue().split('\r\033[2K')[-1]
        self.assertLessEqual(len(line),54)
        self.assertIn('elapsed 8s',line)
        report.finish()
        report,_,err,clock=reporter(terminal=True)
        with patch('shutil.get_terminal_size',return_value=os.terminal_size((20,24))):
            report._draw('Loading image',clock.now(),clock.monotonic()-8,0)
        self.assertEqual(err.getvalue(),'')
        self.assertFalse(report.line_visible)


class ReportingCLITests(unittest.TestCase):
    def cli(self,*args):
        return subprocess.run([sys.executable,str(ROOT/'circle-img.py'),*map(str,args)],
                              capture_output=True,text=True,timeout=15)

    def test_help_and_argument_errors(self):
        run=self.cli('--help')
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('timestamped settings',run.stdout)
        self.assertNotRegex(run.stdout,r'\[\d{4}-\d{2}-\d{2}\]')
        self.assertEqual(run.stderr,'')
        run=self.cli('--not-an-option')
        self.assertEqual(run.returncode,2)
        self.assertRegex(run.stderr,r'\[\d{2}:\d{2}:\d{2}\] error:')
        self.assertNotIn('processing:',run.stdout)

    def test_normal_and_verbose_streams_and_identical_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            outputs=[]
            for mode,flags in (('normal',[]),('short',['-v']),('long',['--verbose'])):
                dest=folder/(mode+'.png')
                run=self.cli('-i',SOURCE,'-o',dest,'--background-layers','2','--preview',*flags)
                self.assertEqual(run.returncode,0,run.stderr)
                self.assertRegex(run.stdout,r'\[\d{2}:\d{2}:\d{2}\] processing:')
                self.assertRegex(run.stdout,r'Saved .*\(256 × 256\); elapsed \d+\.\ds')
                self.assertIn('Saved preview',run.stdout)
                self.assertNotIn('verbose:',run.stdout)
                self.assertNotIn('\x1b',run.stdout+run.stderr)
                self.assertNotIn('\r',run.stdout+run.stderr)
                if flags:
                    for text in ('settings: 2 background layer(s); tolerance 8','file 1 of 1',
                                 'Removing background layer 1','Removed background layer 1',
                                 'Removing background layer 2','Removed background layer 2',
                                 'layer 1: existing alpha','layer 2: visible-boundary RGB',
                                 'output background: #2882AA (automatic)','remaining content: 38 × 32',
                                 'placement:','no resampling','Created and saved preview'):
                        self.assertIn(text,run.stderr)
                    self.assertLess(run.stderr.index('Removing background layer 2'),run.stderr.index('Removed background layer 2'))
                else:
                    self.assertEqual(run.stderr,'')
                with Image.open(dest) as image:
                    outputs.append(image.tobytes())
            self.assertEqual(outputs[0],outputs[1]); self.assertEqual(outputs[1],outputs[2])

    def test_batch_failure_continues_and_preflight_failure_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory); dest=folder/'out'; dest.mkdir()
            missing=folder/'missing.png'
            run=self.cli('-i',missing,SOURCE,'-o',dest,'-v')
            self.assertEqual(run.returncode,1)
            self.assertEqual(run.stdout.count('processing:'),2)
            self.assertIn('file 2 of 2',run.stderr)
            self.assertIn('Failed: Loading image',run.stderr)
            self.assertIn('Saved ',run.stdout)
            self.assertTrue((dest/SOURCE.name).exists())
            run=self.cli('-i',SOURCE,SOURCE,'-o',folder/'bad.png')
            self.assertEqual(run.returncode,1)
            self.assertRegex(run.stderr,r'\[\d{2}:\d{2}:\d{2}\] error:')
            self.assertNotIn('processing:',run.stdout)
            self.assertFalse((folder/'bad.png').exists())

    def test_stage_start_is_flushed_before_detection_and_errors_never_claim_save(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'out.png'
            out,err=Terminal(),Terminal()
            original=app.inner_background
            def inspect_start(*args):
                self.assertIn('processing:',out.getvalue())
                self.assertIn('Removing background layer 2',err.getvalue())
                self.assertNotIn('Removed background layer 2',err.getvalue())
                self.assertGreater(out.flushes,0)
                self.assertGreater(err.flushes,0)
                return original(*args)
            with patch.dict(os.environ,{'TERM':'dumb'}), redirect_stdout(out), redirect_stderr(err):
                with patch.object(app,'inner_background',side_effect=inspect_start), patch.object(app,'save_png',side_effect=OSError('disk error')):
                    status=app.main(['-i',str(SOURCE),'-o',str(output),'--background-layers','2','-v'])
            self.assertEqual(status,1)
            self.assertIn('Failed: Saving PNG',err.getvalue())
            self.assertIn('error:',err.getvalue())
            self.assertNotIn('Saved ',out.getvalue())
            self.assertFalse(output.exists())

    def test_preview_failure_preserves_successful_output_and_interrupt_returns_130(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'out.png'
            original=app.save_png
            def fail_preview(image,path,*args):
                if path.name.endswith('-preview.png'):
                    raise OSError('preview failure')
                return original(image,path,*args)
            out,err=io.StringIO(),io.StringIO()
            with redirect_stdout(out),redirect_stderr(err),patch.object(app,'save_png',side_effect=fail_preview):
                status=app.main(['-i',str(SOURCE),'-o',str(output),'--preview','-v'])
            self.assertEqual(status,1)
            self.assertTrue(output.exists())
            self.assertIn('Saved ',out.getvalue())
            self.assertNotIn('Saved preview',out.getvalue())
            self.assertIn('Failed: Creating and saving preview',err.getvalue())
            out,err=io.StringIO(),io.StringIO()
            with redirect_stdout(out),redirect_stderr(err),patch.object(app,'inner_background',side_effect=KeyboardInterrupt):
                status=app.main(['-i',str(SOURCE),'-o',str(output),'--background-layers','2','-O','-v'])
            self.assertEqual(status,130)
            self.assertIn('Interrupted: Removing background layer 2',err.getvalue())
            self.assertIn('error: interrupted',err.getvalue())
            self.assertNotIn('Saved ',out.getvalue())

    @unittest.skipUnless(os.name=='posix','pseudo-terminal requires POSIX')
    def test_pty_fixture_contract(self):
        code='import sys; print("tty="+str(sys.stderr.isatty()),flush=True); print("marker",file=sys.stderr,flush=True)'
        status,stdout,stderr=terminal_run([sys.executable,'-c',code])
        self.assertEqual(status,0)
        self.assertEqual(stdout,b'tty=True\n')
        self.assertEqual(stderr,'marker\r\n')

    @unittest.skipUnless(os.name=='posix','pseudo-terminal requires POSIX')
    def test_real_cli_terminal_animation_stays_on_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'out.png'
            status,stdout,stderr=terminal_run([sys.executable,str(ROOT/'circle-img.py'),'-i',str(SOURCE),
                                              '-o',str(output),'--background-layers','2'])
            self.assertEqual(status,0,stderr)
            self.assertIn('\r\x1b[2K',stderr)
            self.assertIn('Removing background layer 2... |',stderr)
            self.assertIn('Removed background layer 2',stderr)
            self.assertNotIn(b'\x1b',stdout)
            self.assertTrue(output.exists())


if __name__=='__main__':
    unittest.main()
