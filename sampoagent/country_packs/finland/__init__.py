"""Finland-specific source metadata; core modules remain country-neutral."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDefinition:
    name: str
    url: str
    source_type: str
    capability: str = "Browser search only"


def builtin_sources() -> list[SourceDefinition]:
    return [
        SourceDefinition("Duunitori", "https://duunitori.fi", "job board"),
        SourceDefinition("Työmarkkinatori", "https://tyomarkkinatori.fi", "public-sector board"),
        SourceDefinition("Jobly", "https://jobly.fi", "job board"),
        SourceDefinition("Valtiolle", "https://valtiolle.fi", "public-sector board"),
        SourceDefinition("Kuntarekry", "https://kuntarekry.fi", "public-sector board"),
        SourceDefinition("Barona", "https://barona.fi", "recruitment agency"),
        SourceDefinition("StaffPoint", "https://staffpoint.fi", "recruitment agency"),
        SourceDefinition("Eezy", "https://eezy.fi", "recruitment agency"),
        SourceDefinition("Bolt.Works", "https://bolt.works", "recruitment agency"),
        SourceDefinition("Adecco Finland", "https://adecco.fi", "recruitment agency"),
        SourceDefinition("Manpower Finland", "https://manpower.fi", "recruitment agency"),
        SourceDefinition("Academic Work Finland", "https://academicwork.fi", "recruitment agency"),
        SourceDefinition("Seure", "https://seure.fi", "recruitment agency"),
    ]
