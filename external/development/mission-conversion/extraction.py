"""Render a faction's in-area share of all living players in the mission."""
from briefings import guid


def condition(world, name, area, faction, ratio, seconds=None, *, skip_discovery=False, latch=True, location=None,
              players_only=True, at_most=False, whole_number_quotient=False):
    key = world + '/' + name
    hold = ''
    if seconds:
        options = '  m_bStopAfterFirstChange 1\n' if latch else ''
        if skip_discovery:
            options += '  m_bSkipDiscovery 1\n'
        if location:
            options += f'''  m_Messages GC_CaptureMessages "{{{guid(key + '/messages')}}}" {{
   m_sLocation {location}
   m_sCaptured "The %1 has extracted to %2."
   m_sCapturing "The %1 is extracting to %2."
   m_sCaptureInterrupted "The %1 is no longer extracting to %2."
   m_sLost "The %1 has stopped extraction to %2."
   m_sLosing "The %1 is stopping extraction to %2."
   m_sLossInterrupted "The %1 is no longer stopping extraction to %2."
  }}
'''
        hold = f''' m_Capture GC_AreaCaptureOptions "{{{guid(key + '/hold')}}}" {{
  m_fSeconds {seconds}
  m_bUseElapsedTime 1
  m_iInterruption 3
{options} }}
'''
    who = '' if players_only else '  m_iWho 2\n'
    comparison = '  m_bAtMost 1\n' if at_most else ''
    quotient = '  m_bWholeNumberQuotient 1\n' if whole_number_quotient else ''
    return f'''GC_AreaCondition "{{{guid(key + '/area')}}}" {{
 m_sArea "{area}"
 m_Targets GC_CharacterAreaTargets "{{{guid(key + '/targets')}}}" {{
{who}  m_aFactions {{
   {faction}
  }}
 }}
 m_Requirement GC_LocalFactionShare "{{{guid(key + '/share')}}}" {{
  m_fRequiredShare {ratio}
  m_bWholeMission 1
{comparison}{quotient} }}
{hold}}}
'''
