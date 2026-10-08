#!/bin/bash
sed -n '/Sort by top of stack/,/Binary Images/p' $1 | head -${2:-26}
