# Workbench native updates

GC Lobby owns the dropdown DLL, bootstrap, scripts, build and installer. They
build and run without Reforger MCP. Generic analysis tools may consume exported
inventory data during development; they are not packaged dependencies.

Before updating source, preserve the supported executable's analysis and export
its current native operands with PowerShell 7:

```powershell
./export-native-hook-inventory.ps1 -BaselineSha256 <supported-executable-sha256> -OutputDirectory <new-evidence-directory>
```

The export freezes source files and records hashes, exact source lines and 36
native operands in the 1.9.0.42 implementation. Keep the entire directory.
It excludes the reflected RTTI identifier and is not a semantic C++ parser;
review new native expressions and embedded layout assumptions separately.

For Ghidra comparison, hand `inventory.json` to Reforger MCP's development
`scripts/update-native-hooks.ps1 -HookInventory <file>` with preserved baseline
and current analysis manifests, configuration and a fresh output directory.
That script runs its tests, builds its own analysis tool, retains every candidate
and verifies installed binary identities stayed unchanged. The DLL project
imports no MCP source or executable. Ambiguous/unresolved owners need native
review; a unique match does not establish ABI compatibility.

Update the reviewed native operands and exact compatibility guard, plus the
bootstrap's `HostHash`. Packaging rejects disagreement between the bootstrap and
native guard. Then run `build-workbench-authoring.ps1` with Qt 6.8.3 MSVC x64,
CMake and a fresh output directory. All ten native tests and the faction-field
audit must pass. Keep SDK archives/checksums and build manifests.

Use an isolated test addon to exercise real dropdown selections, literal text,
typed arrays, entity references, history, save/reopen and refusal paths. Restart
Workbench for a new DLL; Reload cannot replace a resident module. Preserve the
user's world, project and settings. `package-workbench-integration.ps1` verifies
source hashes and package independence, then produces a standalone ZIP. Install
into a new version directory and verify normal bootstrap startup, not only a
manual developer load. Retain previous packages and evidence.

## 1.9.0.42 evidence

Host SHA-256: `a81dd4ca52ebbca584d56fe4968c4a12f2f80f2ac7a0c0a176b68ea23294c9b6`.
Native DLL SHA-256: `5e4140b4e583125797b2d6c06d2ba26fec701c0cbb56226916f4b2714e1b9157`.
The 1.9.0.37 comparison found 31 unique operands, one ambiguous accessor and
four unresolved table operands. Caller/type evidence and explicit virtual-table
comparisons resolved those five; the reviewed map is retained in the local
`external/development/update-19042` evidence directory.

Live tests found and fixed a production integration defect: string-array edits
previously registered an empty native history value, so redo lost the selection.
The DLL now links and installs its typed-array command owner and supplies a
process-local prepared value to the guarded script handler. Native history owns
the typed payload after request completion. Tests passed selection, undo/redo,
commas, Unicode, neighboring entries and save/reopen. Scalar entity selection
and its history passed. Automatic startup verified the installed DLL hash,
property hook and menu icons on the exact host.

Further live checks verified unnamed scalar entity selection: generated name
and destination reference were applied together, reverted together by one Undo,
and restored together by one Redo. Manually typed array text containing a comma
also preserved its neighbors and survived undo/redo. These exercised the
installed production DLL and its normal editor signals.

Entity arrays now use the entity provider's identity contract, including
waypoints and named targets. A read-only preparation verifies eligibility,
world, source, local path and complete array contents. Commit revalidates those
identities and adopts the native typed command; naming and the selected entry
share one editor history action. Literal text and None use the same string-array
owner as other arrays. Live tests passed unnamed selection with unchanged
neighbors, grouped undo/redo and save/reopen. Inherited object paths remain
explicitly refused: creating an override would invalidate the captured native
container. Unknown outcomes retain their receipt and disable further edits.
The broad update acceptance and original multi-addon project verification remain
separate from these focused receipts.
