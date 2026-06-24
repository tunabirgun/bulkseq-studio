#!/usr/bin/env perl
# Neutralize semicolons embedded inside double-quoted GTF attribute values
# (e.g. NCBI RefSeq gene symbols like "CYCB1;1"), which gffread mis-parses as
# the attribute separator and aborts ('"' required for GTF). Replace any ";"
# inside a quoted value with "_"; the real "; attribute separators sit outside
# quotes and are left untouched. No-op for GTFs without embedded semicolons.
while (<>) {
    s/("(?:[^"\\]|\\.)*")/(my $x = $1) =~ tr|;|_|; $x/ge;
    print;
}
