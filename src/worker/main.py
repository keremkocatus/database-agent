"""Worker entrypoint — iskelet (design/11). Postgres job-queue tüketici döngüsü M7'de.

M0-M2'de sync inline (CLI) çalışır; bu modül kasıtlı olarak boştur, sözleşmeyi sabitler.
"""

from __future__ import annotations


def main() -> None:
    raise SystemExit(
        "worker M7'de etkinleşir (job-queue + APScheduler enqueue). "
        "M0-M2'de: db-agent sync --inline"
    )


if __name__ == "__main__":
    main()
