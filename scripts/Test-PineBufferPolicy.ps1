$ErrorActionPreference = 'Stop'
# Arithmetic reference fixtures, NOT a Pine interpreter or chart-event test.
$fixtures = @(
    @{ Symbol='MNQ'; Tick=0.25; Floor=8; ATR=10.0; Expected=2.0 },
    @{ Symbol='MNQ'; Tick=0.25; Floor=8; ATR=35.0; Expected=3.5 },
    @{ Symbol='MES'; Tick=0.25; Floor=4; ATR=5.0; Expected=1.0 },
    @{ Symbol='MES'; Tick=0.25; Floor=4; ATR=20.0; Expected=2.0 },
    @{ Symbol='MGC'; Tick=0.1; Floor=10; ATR=7.2; Expected=1.0 },
    @{ Symbol='MGC'; Tick=0.1; Floor=10; ATR=25.0; Expected=2.5 }
)
$checks = 0
foreach ($fixture in $fixtures) {
    $buffer = [Math]::Max($fixture.Tick * $fixture.Floor, 0.1 * $fixture.ATR)
    if ([Math]::Abs($buffer - $fixture.Expected) -gt 0.000001) { throw "Wrong buffer: $($fixture.Symbol)" }
    $checks++
}
$level = 100.0
$buffer = 2.0
# Strict confirmed-close reclaim: equality is not beyond the buffer.
foreach ($case in @(
    @{ Direction=1; Close=98.0; Expected=$false },
    @{ Direction=1; Close=97.75; Expected=$true },
    @{ Direction=-1; Close=102.0; Expected=$false },
    @{ Direction=-1; Close=102.25; Expected=$true },
    @{ Direction=1; Close=101.0; Expected=$false }
)) {
    $reclaimed = ($case.Direction -eq 1 -and $case.Close -lt $level - $buffer) -or ($case.Direction -eq -1 -and $case.Close -gt $level + $buffer)
    if ($reclaimed -ne $case.Expected) { throw 'Wrong reclaim boundary' }
    $checks++
}
# Fractional ATR buffer: outward tick rounding cannot tighten the stop.
$buffer = 2.13
$tick = 0.25
$longStop = [Math]::Floor(($level - $buffer) / $tick) * $tick
$shortStop = [Math]::Ceiling(($level + $buffer) / $tick) * $tick
if ($longStop -ne 97.75 -or $shortStop -ne 102.25) { throw 'Wrong outward stop rounding' }
$checks += 2
Write-Output "PASS: $checks buffer/reclaim/stop arithmetic checks (not Pine execution)."
