structure_unternehmen ="""
- Unternehmen
"""
unternehmen = {
  "title":  "unternehmen",
  "structure": structure_unternehmen,
  "url": "https://www.stadtwerke-waiblingen.de/unternehmen"
}

structure_karriere ="""
- Karriere
"""
karriere = {
  "title":  "karriere",
  "structure": structure_karriere,
  "url": "https://www.stadtwerke-waiblingen.de/karriere"
}

structure_aktuelles ="""
- Aktuelles
"""
aktuelles = {
  "title":  "aktuelles",
  "structure": structure_aktuelles,
  "url": "https://www.stadtwerke-waiblingen.de/aktuelles"
}

structure_stoerung ="""
- Störung
"""
stoerung = {
  "title":  "stoerung",
  "structure": structure_stoerung,
  "url": "https://www.stadtwerke-waiblingen.de/notfallnummern"
}

structure_kontakt ="""
- Kontakt
"""
kontakt = {
  "title":  "kontakt",
  "structure": structure_kontakt,
  "url": "https://www.stadtwerke-waiblingen.de/kontakt"
}

structure_Kundenportal ="""
- Kundenportal (Hier musst du info geben, was das Kundenportal anbietet)
"""
Kundenportal = {
  "title":  "Kundenportal",
  "structure": structure_Kundenportal,
  "url": "https://privatkundenportal.net/waiblingen/"
}

structure_PrivateKunden_Strom ="""
- Strom (Komplette Webpage)
  (Auf der Webpage Strom findest du folgenden Themen, die Themen nacheinander klicken, die Webpage komplett crawlen, nicht mehr tiefer gehen und wieder zurück zu Strom und das nächste Thema klicken)
  - Ökostromtarif 
  - Wärmestrom 
  - Grundversorgung 
  - Preisinformation 
  - Stromkennzeichnung 
"""
Privatkunden_PrivateKunden_Strom = {
  "title":  "PrivateKunden_Strom",
  "structure": structure_PrivateKunden_Strom,
  "url": "https://www.stadtwerke-waiblingen.de/Privatkunden/Strom"
}

structure_PrivateKunden_Strom_2 ="""
(Instruction: crawl each page completely, then go to the next page. Don't miss anything, especially the contact informations. )
- Strom 
  - Preisinformation 
"""
Privatkunden_PrivateKunden_Strom_2 = {
  "title":  "PrivateKunden_Strom_2",
  "structure": structure_PrivateKunden_Strom_2,
  "url": "https://www.stadtwerke-waiblingen.de/Privatkunden/Strom"
}

structure_PrivateKunden_Service_1 ="""
- Service (Root, crawl this page completely, including contact and opening time at the end, then go to the following topics. )
(Instruction: Following topics should be completely crawled according to the given order. )
  - Zählerstand mitteilen
  - Umzugsservice
  - Abrechnung & Zahlung
"""
Privatkunden_Service_1 = {
  "title":  "Privatkunden_Service_1",
  "structure": structure_PrivateKunden_Service_1,
  "url": "https://www.stadtwerke-waiblingen.de/Privatkunden/Strom"
}

structure_PrivateKunden_Service_2 ="""
- Service 
(Instruction: Following topics should be completely crawled carefully according to the given order. )
  - Abschläge berechnen & verstehen
  - Energiesparen
  - Warnung vor Betrugsversuchen
"""
Privatkunden_Service_2 = {
  "title":  "Privatkunden_Service_2",
  "structure": structure_PrivateKunden_Service_2,
  "url": "https://www.stadtwerke-waiblingen.de/Privatkunden/Strom"
}

structure_PrivateKunden_Erdgas ="""
(Instructions: Crawl the complete page for Erdgass, then go to the page Grundversorgung. )
- Erdgas 
  - Grundversorgung
"""
Privatkunden_Erdgas = {
  "title":  "Privatkunden_Erdgas",
  "structure": structure_PrivateKunden_Erdgas,
  "url": "https://www.stadtwerke-waiblingen.de/Privatkunden/Erdgas"
}

structure_PrivateKunden_Wasser ="""
(Instructions: Crawl the complete page for Wasser. Don't go any deeper. )
- Wasser
"""
Privatkunden_Wasser = {
  "title":  "Privatkunden_Wasser",
  "structure": structure_PrivateKunden_Wasser,
  "url": "https://www.stadtwerke-waiblingen.de/Privatkunden/Trinkwasser"
}

structure_PrivateKunden_Waerme_1 ="""
(Instructions: Crawl the complete page for Wärme. After that go to the page Fernwärme and also crawl everything. )
- Wärme
  - Fernwärmes (Make sure to crawl everything from Alle Infos zur Versorgung mit Fernwärme)
"""
Privatkunden_Waerme_1 = {
  "title":  "Privatkunden_Waerme_1",
  "structure": structure_PrivateKunden_Waerme_1,
  "url": "https://www.stadtwerke-waiblingen.de/Privatkunden/Waerme"
}

structure_PrivateKunden_Waerme_2 ="""
(Instructions: base is Wärme, ignore content in this base completely, go to the Mobile Heizentrale mieten directly and crawl the content carefully. )
- Wärme
  - Mobile Heizzentralen mieten
"""
Privatkunden_Waerme_2 = {
  "title":  "Privatkunden_Waerme_2",
  "structure": structure_PrivateKunden_Waerme_2,
  "url": "https://www.stadtwerke-waiblingen.de/Privatkunden/Waerme"
}

structure_PrivateKunden_E_Mobilitaet ="""
(Instructions: crawl everything exactly as it is from the webpage. Expandable elements musst be expanded!)
- E-Mobilität (Make sure to crawl all the questions (begin with was, wo, wie and so funktioniert das E-Carsharing) carefully and completetly, click to get the answer for the questions. )
  - Anmeldung E-Ladestation 
"""
Privatkunden_E_Mobilitaet = {
  "title":  "Privatkunden_E_Mobilitaet",
  "structure": structure_PrivateKunden_E_Mobilitaet,
  "url": "https://www.stadtwerke-waiblingen.de/Privatkunden/E-Mobilitaet"
}

structure_PrivateKunden_Baeder ="""
(Instructions: crawl everything exactly as it is from the webpage. Expandable elements musst be expanded! Don't go deeper. )
- Bäder
"""
Privatkunden_Baeder = {
  "title":  "Privatkunden_Baeder",
  "structure": structure_PrivateKunden_Baeder,
  "url": "https://www.stadtwerke-waiblingen.de/Privatkunden/Baeder"
}

structure_Netze_Übersicht ="""
(Instructions: crawl everything exactly as it is from the webpage. Expandable elements must be expanded! Don't go deeper. )
- Netze (Craw everything from top to bottom, including the three expandable elements: Vertragsinstallateure Strom, Vertragsinstallateure Gas and Vertragsinstallateure Wasser)
"""
Netze_Übersicht = {
  "title":  "Netze_Übersicht",
  "structure": structure_Netze_Übersicht,
  "url": "https://www.stadtwerke-waiblingen.de/Netze/Uebersicht-Netze"
}

structure_Netze_Strom_1 ="""
(Instructions: crawl everything completely from the webpage from top to bottom. 
Go to the next webpage only when you are finished with the current webpage. 
Use Markdown format all the time. 
Expandable elements must be expanded! Don't go deeper than the structure. 
Do not ignore Telefon number. )
- Strom (crawl this page from top to bottom first, then go to following topics.)
  - Anmeldung E-Ladestation (Do not ignore the files listed, give summary to the formalar.)
  - Netzanschluss
  - Netzzugang&Verträge
  - Netznutzungsentgelte
"""
Netze_Strom_1 = {
  "title":  "Netze_Strom_1",
  "structure": structure_Netze_Strom_1,
  "url": "https://www.stadtwerke-waiblingen.de/Netze/Stromnetz"
}

structure_Netze_Strom_2 ="""
(Instructions: scan everything completely from the webpage from top to bottom. 
Go to the next webpage only when you are finished with the current webpage. 
Use Markdown format all the time. 
- Strom 
  - Netzstrukturdaten 
  - Grund- & Ersatzversorgung (remember to expand all expandable elements in Grundversorgung, after click, wait for 0.5 seconds for the content to show up. )
"""
Netze_Strom_2 = {
  "title":  "Netze_Strom_2",
  "structure": structure_Netze_Strom_2,
  "url": "https://www.stadtwerke-waiblingen.de/Netze/Stromnetz"
}

structure_Netze_Strom_3 ="""
(Instructions: scan everything completely from the webpage from top to bottom. )
Use Markdown format all the time.
  - EEG & Einspeiser (remember to expand all expandable elements in Erforderliche Nachweise und Formulare and Informationen für Anlagenbetreiber, wait for 0.5 seconds for the content to show up. )
"""
Netze_Strom_3 = {
  "title":  "Netze_Strom_3",
  "structure": structure_Netze_Strom_3,
  "url": "https://www.stadtwerke-waiblingen.de/Netze/Stromnetz/EEG-Einspeiser"
}

structure_Netze_Erdgas_1 ="""
- Erdgas
  - Netzanschluss
  - Netzzugang & Verträge
  - Netznutzungsentgelte
  - Netzstrukturdaten (here don't miss to expand the expandable elements in Allgemeine Information)
  - Grund-&Ersatzversorgung
"""
Netze_Erdgas_1 = {
  "title":  "Netze_Erdgas_1",
  "structure": structure_Netze_Erdgas_1,
  "url": "https://www.stadtwerke-waiblingen.de/Netze/Gasnetz"
}

structure_Netze_Erdgas_2 ="""
- Erdgas (don't crawl this page, go to the subtopics directly. )
  - Netzstrukturdaten
  - Grund-&Ersatzversorgung
"""
Netze_Erdgas_2 = {
  "title":  "Netze_Erdgas_2",
  "structure": structure_Netze_Erdgas_2,
  "url": "https://www.stadtwerke-waiblingen.de/Netze/Gasnetz"
}

structure_Netze_Wasser ="""
- https://www.stadtwerke-waiblingen.de/Netze/Wassernetz
"""
Netze_Wasser = {
  "title":  "Netze_Wasser",
  "structure": structure_Netze_Wasser,
  "url": "https://www.stadtwerke-waiblingen.de/Netze/Wassernetz"
}

structure_Netze_Glassfaser ="""
- Glasfaser
"""
Netze_Glassfaser = {
  "title":  "Netze_Glassfaser",
  "structure": structure_Netze_Glassfaser,
  "url": "https://www.stadtwerke-waiblingen.de/Netze/Glasfasernetz"
}

structure_Netze_Messstellenbetrieb ="""
- Messstellenbetrieb (make sure to crawl everything, pay more attention to the expandable FAQ part, make sure you get all its content, because it is never empty. )
"""
Netze_Messstellenbetrieb = {
  "title":  "Netze_Messstellenbetrieb",
  "structure": structure_Netze_Messstellenbetrieb,
  "url": "https://www.stadtwerke-waiblingen.de/Netze/Messstellenbetrieb"
}

structure_Netze_Planauskunft ="""
- Planauskunft (Instructions: scan everything on this page completely from top to bottom. 
  - Zur Planauskunft (only provide the summary of the formular)
"""
Netze_Planauskunft = {
  "title":  "Netze_Planauskunft",
  "structure": structure_Netze_Planauskunft,
  "url": "https://www.stadtwerke-waiblingen.de/Netze/Planauskunft"
}

structure_Geschaeftskunden_Strom ="""
(Instruction: crawl each page completely, then go to the next page. Don't miss anything, especially the contact informations. )
(Make sure to expand all the expandable elements, this is very important. )
- Strom
  - Grundversorgung
  - Preisinformation
  - Stromkennzeichnung
"""
Geschaeftskunden_Strom = {
  "title":  "Geschaeftskunden_Strom",
  "structure": structure_Geschaeftskunden_Strom,
  "url": "https://www.stadtwerke-waiblingen.de/Geschäftskunden/Strom"
}

structure_Geschaeftskunden_Erdgas_Grundversorgung ="""
(Instruction: crawl the base page completely, then go to the next page. Don't miss anything, especially the contact informations. )
- Erdgas
  - Grundversorgung
"""
Geschaeftskunden_Erdgas_Grundversorgung = {
  "title":  "Geschaeftskunden_Erdgas_Grundversorgung",
  "structure": structure_Geschaeftskunden_Erdgas_Grundversorgung,
  "url": "https://www.stadtwerke-waiblingen.de/Geschäftskunden/Erdgas"
}

structure_Geschaeftskunden_Wasser ="""
(Instruction: Don't miss anything, especially the contact informations. )
(Make sure to expand all the expandable elements, this is very important. )
- Wasser
"""
Geschaeftskunden_Wasser = {
  "title":  "Geschaeftskunden_Wasser",
  "structure": structure_Geschaeftskunden_Wasser,
  "url": "https://www.stadtwerke-waiblingen.de/Geschäftskunden/Wasser"
}

structure_Geschaeftskunden_Dienstleistungen ="""
(Instruction: crawl each page completely, then go to the next page. Don't miss anything, especially the contact informations. )
- Dienstleistungen
  - Energielieferung Strom und Gas (crawl the contact information. )
  - E-Mobilität – bei der NEW
  - Photovoltaik-Anlagen (generate a summary)
  - Glasfaseranschluss (Make sure to expand all the expandable elements in this part, this is very important. )
"""
Geschaeftskunden_Dienstleistungen = {
  "title":  "Geschaeftskunden_Dienstleistungen",
  "structure": structure_Geschaeftskunden_Dienstleistungen,
  "url": "https://www.stadtwerke-waiblingen.de/Geschäftskunden/Dienstleistungen"
}

structure_Geschaeftskunden_Service ="""
(Instruction: crawl each page completely, then go to the next page. Don't miss anything, especially the contact informations. )
- Service (This page should be completely crawled. )
  - Zählerstand mitteilen 
  - Kundenportal (Don't miss this! )
  - Abrechnung & Zahlung (Make sure to expand all the expandable elements, this is very important. )
"""
Geschaeftskunden_Service = {
  "title":  "Geschaeftskunden_Service",
  "structure": structure_Geschaeftskunden_Service,
  "url": "https://www.stadtwerke-waiblingen.de/Privatkunden/Service"
}

# ← change this line to switch crawl target
active_topic = kontakt
structure = {
    active_topic.get("title"): {
        "url": active_topic.get("url"),
        "subpart": [active_topic.get("structure")],
    },
}