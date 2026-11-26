// =====================================================================
//  AirPods Pro 3 Halter fürs Bett / die Wand
//  - Spalt im Boden: Staub und Krümel fallen durch statt sich zu sammeln
//  - 3 Befestigungen: Klebeband, versenkte Schrauben, Bettrahmen-Clip
//  - Deckel lässt sich im Halter aufklappen (Wände bleiben unter der Deckelfuge)
//
//  Gedruckt wird aufrecht (Boden auf dem Druckbett), ohne Stützmaterial.
//  Kompatibel mit dem MakerWorld "Parametric Model Maker" (OpenSCAD-Customizer).
// =====================================================================

/* [Befestigung] */
// Wie soll der Halter befestigt werden?
befestigung = "klebeband"; // [klebeband:Doppelseitiges Klebeband, schraube:Schrauben (versenkt), clip:Bettrahmen-Clip zum Einhängen]

/* [Bettrahmen-Clip] */
// Dicke des Bettrahmens / Kopfteils, über den der Clip gehängt wird (mm)
rahmen_dicke = 25; // [10:0.5:60]
// Zusätzliches Spiel im Clip (mm)
clip_spiel = 0.6; // [0:0.1:2]
// Stärke von Steg und hinterem Bein (mm)
clip_staerke = 3.5; // [2.5:0.5:6]

/* [Schrauben] */
// Schraubendurchmesser (mm), z. B. 3,5 oder 4 mm Holzschraube
schraube_d = 4; // [3:0.5:5]
// Kopfdurchmesser der Senkkopfschraube (mm)
schraube_kopf_d = 8; // [5:0.5:10]
// Abstand der beiden Schrauben (mm)
schraube_abstand = 20; // [10:1:26]

/* [AirPods-Case] */
// Breite des Ladecase (Apple-Angabe: 62,2 mm)
case_breite = 62.2; // [55:0.1:70]
// Tiefe des Ladecase (Apple-Angabe: 21,8 mm)
case_tiefe = 21.8; // [18:0.1:28]
// Spiel pro Seite (mm)
spiel = 0.5; // [0.2:0.1:1.5]
// Dicke einer Schutzhülle, falls du eine benutzt (mm, 0 = ohne Hülle)
huelle = 0; // [0:0.1:3]

/* [Halter] */
// Seitenwand-Stärke (mm)
wand = 2.4; // [1.6:0.2:4]
// Boden-Stärke (mm)
boden = 2.4; // [1.6:0.2:4]
// Rückwand-Stärke (mm)
rueckwand = 3.2; // [2.4:0.2:6]
// Wandhöhe über dem Boden (mm). Bis ca. 28 mm lässt sich der Deckel im Halter aufklappen.
wand_hoehe = 26; // [15:1:40]
// Breite des Spalts im Boden (mm), 0 = kein Spalt
spalt_breite = 8; // [0:0.5:12]
// Länge des Spalts im Boden (mm)
spalt_laenge = 44; // [10:1:50]
// Breite des Ausschnitts vorne (mm)
front_breite = 34; // [20:1:44]
// Höhe der vorderen Lippe über dem Boden (mm)
front_lippe = 8; // [4:1:20]

/* [Hidden] */
$fn = 96;
eps = 0.01;

// ---------------------------------------------------------------------
// Abgeleitete Maße
// ---------------------------------------------------------------------
gap = spiel + huelle;
W   = case_breite + 2 * gap;        // Innenbreite
D   = case_tiefe  + 2 * gap;        // Innentiefe
R_f = 8 + gap;                      // Eckenradius unten (Frontansicht), kleiner als am Case
R_t = 7 + gap;                      // Eckenradius (Draufsicht), kleiner als am Case
H   = boden + wand_hoehe;           // Gesamthöhe

// Senkkopf braucht etwas Fleisch: Rückwand bei Schraubversion ggf. dicker
senk_tiefe = (schraube_kopf_d + 0.4 - (schraube_d + 0.3)) / 2;
rw = (befestigung == "schraube") ? max(rueckwand, senk_tiefe + 0.3 + 1.2) : rueckwand;

y0 = rw;                            // Innenseite Rückwand
Wo = W + 2 * wand;                  // Außenbreite
Do = rw + D + wand;                 // Außentiefe

spalt_L = min(spalt_laenge, W - 2 * R_f);
spalt_B = min(spalt_breite, D - 8);

// ---------------------------------------------------------------------
// Hilfsmodule
// ---------------------------------------------------------------------
// zentriertes Rechteck mit runden Ecken
module rrect(w, d, r) {
    offset(r = r) square([w - 2 * r, d - 2 * r], center = true);
}

// 2D-Form (x,z) entlang Y von a bis b extrudieren
module extrude_y(a, b) {
    translate([0, b, 0]) rotate([90, 0, 0]) linear_extrude(b - a) children();
}

// Kreis mit 45°-Spitze nach unten -> druckbar ohne Überhang
module teardrop2d(r) {
    hull() {
        circle(r = r);
        translate([0, -r * sqrt(2)]) square(0.02, center = true);
    }
}

// ---------------------------------------------------------------------
// Geometrie
// ---------------------------------------------------------------------
// Frontansicht der Tasche: unten abgerundet, oben offen
module pocket_front2d() {
    hull() {
        for (s = [-1, 1]) translate([s * (W / 2 - R_f), boden + R_f]) circle(r = R_f);
        translate([-W / 2, boden + R_f]) square([W, H]);
    }
}

// Frontansicht außen: runde Kanten, unten 45° auslaufend, flacher Fuß
module outer_front2d() {
    intersection() {
        translate([-Wo / 2 - 1, 0]) square([Wo + 2, H]);
        hull() {
            for (s = [-1, 1]) translate([s * (W / 2 - R_f), boden + R_f]) teardrop2d(R_f + wand);
            translate([-Wo / 2, boden + R_f]) square([Wo, H]);
            translate([-(W / 2 - R_f), 0]) square([W - 2 * R_f, 1]);
        }
    }
}

// Draufsicht außen: vorne rund, hinten komplett flach (Klebefläche)
module outer_top2d() {
    intersection() {
        hull() {
            translate([0, y0 + D / 2]) rrect(W + 2 * wand, D + 2 * wand, R_t + wand);
            translate([-Wo / 2, 0]) square([Wo, y0 + D / 2]);
        }
        translate([-Wo, 0]) square([2 * Wo, 2 * Do]);
    }
}

module body() {
    intersection() {
        extrude_y(0, Do) outer_front2d();
        linear_extrude(H) outer_top2d();
    }
}

module pocket() {
    intersection() {
        extrude_y(y0 - 1, y0 + D + 1) pocket_front2d();
        translate([0, 0, boden - 1]) linear_extrude(H + 10)
            translate([0, y0 + D / 2]) rrect(W, D, R_t);
    }
    // Einführschräge oben
    c = 0.8;
    hull() {
        translate([0, 0, H - c]) linear_extrude(eps) translate([0, y0 + D / 2]) rrect(W, D, R_t);
        translate([0, 0, H]) linear_extrude(1) translate([0, y0 + D / 2]) rrect(W + 2 * c, D + 2 * c, R_t + c);
    }
}

// Spalt im Boden gegen Staub & Krümel
module bottom_slot() {
    if (spalt_B > 0)
        translate([0, y0 + D / 2, -1]) linear_extrude(boden + 2)
            hull() for (s = [-1, 1]) translate([s * (spalt_L / 2 - spalt_B / 2), 0]) circle(d = spalt_B);
}

// U-förmiger Ausschnitt vorne (Greifen, LED sichtbar)
module front_cut() {
    fz  = boden + front_lippe;
    r_u = min(front_breite / 2, 8);
    rf  = 3;
    extrude_y(y0 + D / 2, Do + 1)
        offset(r = -rf) offset(delta = rf) union() {
            hull() {
                for (s = [-1, 1]) translate([s * (front_breite / 2 - r_u), fz + r_u]) circle(r = r_u);
                translate([-front_breite / 2, fz + r_u]) square([front_breite, H + 10]);
            }
            translate([-Wo, H]) square([2 * Wo, 10]);
        }
}

// Zwei versenkte Schraubenlöcher in der Rückwand
// (Zugang mit dem Schraubendreher durch den Front-Ausschnitt)
module screw_holes() {
    zc  = boden + front_lippe + (wand_hoehe - front_lippe) / 2;
    d_l = schraube_d + 0.3;
    d_k = schraube_kopf_d + 0.4;
    k   = (d_k - d_l) / 2;
    for (s = [-1, 1]) translate([s * schraube_abstand / 2, 0, zc]) rotate([-90, 0, 0]) {
        translate([0, 0, -1]) cylinder(d = d_l, h = rw + 2);
        translate([0, 0, rw - 0.3 - k]) cylinder(d1 = d_l, d2 = d_k, h = k + eps);
        translate([0, 0, rw - 0.3]) cylinder(d = d_k, h = 2);
    }
}

// U-Haken zum Einhängen über Bettrahmen / Kopfteil
// (Steg oben bündig mit der Wandoberkante, damit der Deckel frei bleibt)
module clip() {
    T  = rahmen_dicke + clip_spiel;
    t  = clip_staerke;
    yb = -(T + t);
    difference() {
        intersection() {
            extrude_y(yb, 0.5) outer_front2d();
            linear_extrude(H) hull() {
                translate([-Wo / 2, 0]) square([Wo, 0.5]);
                for (s = [-1, 1]) translate([s * (Wo / 2 - 2), yb + 2]) circle(r = 2);
            }
        }
        translate([-Wo, -T, -1]) cube([2 * Wo, T, H - t + 1]);
    }
}

module halter() {
    difference() {
        union() {
            body();
            if (befestigung == "clip") clip();
        }
        pocket();
        bottom_slot();
        front_cut();
        if (befestigung == "schraube") screw_holes();
    }
}

halter();
