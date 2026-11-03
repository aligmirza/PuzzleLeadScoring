"""Calibration of the services pipeline on labelled companies (enrich/pipeline/calibrate.py).

    .venv/bin/python -m unittest discover -s tests -v

Synthetic results in a temp list: nothing real is read or changed (no --apply here).
"""
import csv
import tempfile
import unittest
from pathlib import Path

from enrich.core import icp


def build(tmp: Path, groups: dict[str, tuple[int, tuple[str, ...]]]) -> Path:
    """A temp list with services results: per label, (how many companies, which facts are Yes)."""
    from enrich.checks.rules import SIGNALS
    from enrich.core.store import Store
    folder = tmp / "lst"
    store = Store(folder)
    labels = tmp / "labels.csv"
    with open(labels, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Domain", "Buyer type", "Outcome"])
        for label, (n, facts) in groups.items():
            for i in range(n):
                d = f"{label}{i}.com"
                s = {sid: {"value": "yes" if sid in facts else "unknown"} for sid in SIGNALS}
                with icp.use("services"):
                    store.save_result(d, {"icp_verdict": "Yes", "signals": s, "facts": {"segment": "SMB"}}, "icp")
                    store.save_result(d, {"signals": s, "facts": {"segment": "SMB"}}, "signals")
                w.writerow([d, label, "closed_won" if label == "help" and i < 3 else ""])
    return folder, labels


class Calibrate(unittest.TestCase):
    def test_triggers_separate_help_from_diy(self):
        from enrich.pipeline import calibrate
        tmp = Path(tempfile.mkdtemp())
        folder, labels = build(tmp, {"help": (8, ("B9", "F9", "S3")), "diy": (8, ("S5", "F10"))})
        with icp.use("services"):
            r = calibrate.run(folder, labels)
        self.assertEqual((r["help"], r["diy"], r["closed_won"]), (8, 8, 3))
        self.assertGreater(r["separation_suggested"], 0.9)
        facts = {f["id"]: f for f in r["facts"]}
        self.assertEqual(facts["B9"]["suggested"], "high")
        self.assertEqual(facts["F10"]["suggested"], "none")  # DIY buyers have it, help-seekers don't
        self.assertTrue(facts["F1"]["suggested"].startswith("keep"))  # nobody has it: too few to judge
        self.assertEqual(r["help_at_or_above_cut"], 8)
        self.assertEqual(r["diy_at_or_above_cut"], 0)
        self.assertTrue((folder / "output" / "calibration.json").exists())

    def test_only_for_services(self):
        from enrich.pipeline import calibrate
        tmp = Path(tempfile.mkdtemp())
        folder, labels = build(tmp, {"help": (1, ("B9",)), "diy": (1, ())})
        with icp.use("product"):
            with self.assertRaises(SystemExit):
                calibrate.run(folder, labels)

    def test_template(self):
        from enrich.pipeline import calibrate
        p = calibrate.write_template(Path(tempfile.mkdtemp()) / "labels.csv")
        with open(p) as f:
            self.assertEqual(next(csv.reader(f)), ["Domain", "Company", "Buyer type", "Outcome", "Notes"])
        self.assertEqual(len(calibrate.read_labels(p)), 2)


if __name__ == "__main__":
    unittest.main()
