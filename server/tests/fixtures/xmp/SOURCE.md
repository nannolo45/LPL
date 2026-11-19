Real darktable sidecars, copied unchanged from darktable's integration tests
(https://github.com/darktable-org/darktable-tests, commit 968d9a7246450e6a34217ea8f06f5221f37bceeb, GPL-3.0):

| file | from | why |
|---|---|---|
| nop.xmp | 0000-nop | darktable's default pipeline only (auto-applied modules) |
| exposure.xmp | 0001-exposure | nop + one exposure item: "exposure added" |
| orientation-minus-90.xmp | 0005-orientation-minus-90 | the same instance (flip) twice in the history, off then on |
| blending-modes-uniform.xmp | 0033-blending-modes-uniform | five exposure instances, two color balance instances (multi_priority, multi_name) |
| capture-sharpen.xmp | 0171-capture-sharpen | xmp_version 5 (darktable 5), preset names as instance names (`_builtin_...`) |

The tests derive two more from these in code (never written to this folder): one with `history_end`
before the last item, and one without any history (as darktable writes for an image that was never edited).
