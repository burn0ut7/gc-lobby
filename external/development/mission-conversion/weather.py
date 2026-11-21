"""Convert legacy mode weather overrides into native and Lobby settings."""
from datetime import date
from decimal import Decimal
import re

from briefings import block_end, guid

ENVIRONMENT = {
    'm_fFogDensity': ('m_fFogAmount', 1),
    'm_fFogHeight': ('m_fFogHeightDensity', 150),
    'm_fRain': ('m_fRainIntensity', 1),
    'm_fWind': ('m_fWindSpeed', 50),
}
DATE = {
    'm_iSetDay': ('m_iDay', '1'),
    'm_iSetMonth': ('m_iMonth', '8'),
    'm_iSetYear': ('m_iYear', '1989'),
}
LEGACY_SWITCHES = {
    'm_bOverrideGeocoords': {'m_fLatitude', 'm_fLongitude'},
    'm_bOverrideTimeZoneInfo': {'m_fUTCTimeZone', 'm_fDSTEnabled', 'm_fDSTOffsetHours'},
}
NATIVE_FIELDS = set('''
    Enabled m_iStartingHours m_iStartingMinutes m_bRandomStartingDaytime
    m_bRandomStartingDaytimeRange m_iRangeHoursStart m_iRangeMinutesStart
    m_iRangeHoursEnd m_iRangeMinutesEnd m_fDayTimeAcceleration m_fNightTimeAcceleration
    m_bRandomStartingWeather m_bUsePredefineStartingTimeAndWeather m_aStartingWeatherAndTime
    m_bRandomWeatherChanges m_bAllowHeaderSettings m_bWindOverride m_fWindSpeed m_fWindDirection
    m_bFogOverride m_fFogAmount m_fFogHeightDensity m_bDateOverride m_iDay m_iMonth m_iYear
'''.split())


def environment_component(world, values):
    return '\n'.join([f'  GC_MissionEnvironmentComponent "{{{guid(world + "/environment")}}}" {{',
                      *(f'   {key} {value}' for key, value in values.items()), '  }', ''])


def convert_weather(world, lines):
    fields = {}
    chunks = {}
    index = 1
    while index < len(lines) - 1:
        field, _, value = lines[index].strip().partition(' ')
        if not field or field in fields:
            raise ValueError('invalid or duplicate weather field ' + field)
        stop = block_end(lines, index) if value == '{' else index
        fields[field] = value
        chunks[field] = lines[index:stop + 1]
        index = stop + 1

    def enabled(field):
        value = fields.get(field, '0')
        if value not in ('0', '1'):
            raise ValueError('invalid weather switch ' + field)
        return value == '1'

    omit = {'m_bUseSpecifiedDate', 'm_bOverrideEnvironment', *DATE, *ENVIRONMENT}
    for switch, inactive in LEGACY_SWITCHES.items():
        if enabled(switch):
            raise ValueError('legacy weather requires ' + switch)
        omit.update({switch, *inactive})
    unknown = fields.keys() - omit - NATIVE_FIELDS
    if unknown:
        raise ValueError('legacy weather field needs review: ' + ', '.join(sorted(unknown)))

    output = []
    if enabled('m_bUseSpecifiedDate'):
        date_values = {native: fields.get(legacy, default) for legacy, (native, default) in DATE.items()}
        try:
            date(*(int(date_values[field]) for field in ('m_iYear', 'm_iMonth', 'm_iDay')))
        except ValueError as exc:
            raise ValueError('legacy weather has an invalid date') from exc
        omit.update({'m_bDateOverride', *date_values})
        output.extend(['   m_bDateOverride 1', *(f'   {key} {value}' for key, value in date_values.items())])
    environment = ''
    if enabled('m_bOverrideEnvironment'):
        if enabled('m_bFogOverride') or enabled('m_bWindOverride'):
            raise ValueError('combined native and legacy environmental overrides need review')
        values = {}
        for legacy, (native, limit) in ENVIRONMENT.items():
            value = fields.get(legacy, '1')
            if not re.fullmatch(r'\d+(?:\.\d+)?', value) or Decimal(value) > limit:
                raise ValueError('legacy weather value out of range: ' + legacy)
            values[native] = value
        environment = environment_component(world, values)
    header = '  SCR_TimeAndWeatherHandlerComponent "{6B71E9A8B1A8C501}" {'
    if re.search(r'\b(?:TILW|PS)_\w+', '\n'.join(lines)):
        raise ValueError('custom legacy weather component')
    return '\n'.join([header, *(line for key, chunk in chunks.items() if key not in omit for line in chunk),
                      *output, '  }', '']), environment
