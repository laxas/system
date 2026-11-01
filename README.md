# Linux System Work

This repo is only for work with configuration ans stuff on this linux ubuntu 26.04 VM. The host is a Windows 11. I am using vmware player in the latest version. 

## Usage

    uv run system

Collects basic system info (OS, hardware, CPU, memory, disks, network, failed services) and writes it to `reports/<hostname>/system-info.json`. Each machine gets its own directory, so the repo can be cloned and run on several systems; git history keeps older snapshots.
