## What changed / 修改内容

<!-- Explain the user-visible or reviewer-visible change. / 说明用户或评委能看到的变化。 -->

## Why / 修改原因

<!-- State the problem and intended outcome. / 说明问题与期望结果。 -->

## Verification / 核验

- [ ] I ran `python -I -B ./scripts/package/seal_package.py --write` and committed any generated diff.
- [ ] I ran `python -I -B ./scripts/package/seal_package.py --check` after generation.
- [ ] I ran `./verify_offline.ps1 -Mode Stdlib` or explained why it does not apply.
- [ ] I ran `./verify_offline.ps1 -Mode Full` when locked dependencies were available, or explained why it was not run.
- [ ] I reviewed the generated changes to `reference-source-pins.json`, `PACKAGE_MANIFEST.json`, and `SHA256SUMS.txt` instead of editing them by hand.
- [ ] I recorded the exact commands and outcomes below.

```text
Commands and results / 命令与结果：

```

## Safety and evidence boundaries / 安全与证据边界

> Suspected vulnerabilities or Secret exposures must not be disclosed in this Pull Request. Follow [SECURITY.md](../SECURITY.md). / 不要在本 Pull Request 中披露疑似漏洞或 Secret 泄露细节，请按 [SECURITY.md](../SECURITY.md) 私下报告。

- [ ] This change contains no `.env` file, API key, token, password, private key, personal data, or private runtime material.
- [ ] Any logs, screenshots, examples, and evidence are sanitized and appropriate for public release.
- [ ] Claims distinguish offline verification, reference-environment reproduction, and live Provider execution.
- [ ] No Docker, network, Provider, or Secret access is implied unless the PR supplies explicit evidence.

## Compatibility / 兼容性

- [ ] I described any breaking contract, schema, CLI, or evidence-format change.
- [ ] I considered Windows PowerShell 5.1, Python 3.12, and LF line endings.

## Related issue / 关联 Issue

<!-- Example: Closes #123 -->
