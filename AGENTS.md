# Project Working Preferences

- Do not include `.yaml` files in Git commits or pull requests. Check staged paths and the final PR diff before publishing. To remove an already submitted YAML change, restore the base-branch content instead of deleting an existing configuration file.
- Before creating or updating a pull request, validate the encoding of every added or modified text file in the PR diff. Decode files strictly as UTF-8 (without replacement characters), scan for Unicode replacement characters and common mojibake sequences, and manually inspect changed user-visible Chinese text in the rendered diff.
- Submit PR titles and descriptions as explicit UTF-8 bytes. After creating or updating a PR, read the title and description back from the remote API or rendered page and verify the expected Chinese text exactly; a successful API response is not sufficient.
- Never treat successful tests or normal-looking terminal output as sufficient encoding validation. Read text with an explicit UTF-8 encoding or validate the raw bytes; PowerShell's default `Get-Content` rendering may use a different code page.
- When synchronizing text files to `Elainabot_v2-modules`, compare the source and destination after strict UTF-8 decoding and CRLF/LF normalization. Do not rely only on raw file hashes, because line-ending differences can hide whether the actual text is equivalent.
- Do not create or update the pull request until the UTF-8 validation, mojibake scan, and normalized source/destination comparison all pass.
