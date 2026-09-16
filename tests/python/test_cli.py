import io
import sys
import unittest
from contextlib import redirect_stdout
from tests.python.conftest import block_network
from src.leaps_scanner.cli import main, format_ascii_table


class TestCLI(unittest.TestCase):
    def setUp(self):
        block_network()

    def test_format_ascii_table(self):
        headers = ["Symbol", "Strike", "P_exec", "Status"]
        rows = [
            ["AAPL270115C00150000", "$150.0", "$77.25", "PASS"],
            ["AAPL270115C00180000", "$180.0", "$53.00", "WATCH"]
        ]
        table_str = format_ascii_table("Deep ITM", headers, rows)
        self.assertIn("Deep ITM", table_str)
        self.assertIn("AAPL270115C00150000", table_str)
        self.assertIn("PASS", table_str)

    def test_cli_main_execution(self):
        f = io.StringIO()
        with redirect_stdout(f):
            ret = main(["--symbols", "AAPL,SPY", "--alpha", "0.5", "--offline"])
        output = f.getvalue()
        self.assertEqual(ret, 0)
        self.assertIn("LEAPS CALL QUANT SCANNER", output)
        self.assertIn("Deep ITM Stock Replacement", output)
        self.assertIn("AAPL", output)


if __name__ == "__main__":
    unittest.main()
