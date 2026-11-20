"""Render a faction's in-area share of all living players in the mission."""
from briefings import guid


def condition(world, name, area, faction, ratio, seconds=None):
    key = world + '/' + name
    hold = ''
    if seconds:
        hold = f''' m_Capture GC_AreaCaptureOptions "{{{guid(key + '/hold')}}}" {{
  m_fSeconds {seconds}
  m_bUseElapsedTime 1
  m_iInterruption 3
  m_bStopAfterFirstChange 1
 }}
'''
    return f'''GC_AreaCondition "{{{guid(key + '/area')}}}" {{
 m_sArea "{area}"
 m_Targets GC_CharacterAreaTargets "{{{guid(key + '/targets')}}}" {{
  m_aFactions {{
   {faction}
  }}
 }}
 m_Requirement GC_LocalFactionShare "{{{guid(key + '/share')}}}" {{
  m_fRequiredShare {ratio}
  m_bWholeMission 1
 }}
{hold}}}
'''
