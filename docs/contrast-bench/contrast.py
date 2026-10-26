import math, sys

def hex2rgb(h):
    h = h.lstrip('#'); return tuple(int(h[i:i+2], 16) / 255 for i in (0, 2, 4))

def lin(c): return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def lum(h):
    r, g, b = (lin(c) for c in hex2rgb(h)); return 0.2126*r + 0.7152*g + 0.0722*b

def ratio(a, b):
    la, lb = lum(a), lum(b); hi, lo = max(la, lb), min(la, lb); return (hi + 0.05) / (lo + 0.05)

def over(fg, alpha, bg):
    f, b = hex2rgb(fg), hex2rgb(bg)
    return '#' + ''.join(f'{round((alpha*x + (1-alpha)*y)*255):02x}' for x, y in zip(f, b))

def lab(h):
    r, g, b = (lin(c) for c in hex2rgb(h))
    x = (0.4124*r + 0.3576*g + 0.1805*b) / 0.95047
    y = (0.2126*r + 0.7152*g + 0.0722*b)
    z = (0.0193*r + 0.1192*g + 0.9505*b) / 1.08883
    f = lambda t: t ** (1/3) if t > 216/24389 else (24389/27*t + 16) / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116*fy - 16, 500*(fx - fy), 200*(fy - fz)

def de2000(h1, h2):
    L1, a1, b1 = lab(h1); L2, a2, b2 = lab(h2)
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2); Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb**7 / (Cb**7 + 25**7)))
    a1p, a2p = a1*(1+G), a2*(1+G)
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360; h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dL, dC = L2 - L1, C2p - C1p
    dh = h2p - h1p
    if C1p*C2p == 0: dh = 0
    elif dh > 180: dh -= 360
    elif dh < -180: dh += 360
    dH = 2*math.sqrt(C1p*C2p)*math.sin(math.radians(dh/2))
    Lb, Cbp = (L1+L2)/2, (C1p+C2p)/2
    hs = h1p + h2p
    if C1p*C2p == 0: hb = hs
    elif abs(h1p-h2p) <= 180: hb = hs/2
    else: hb = (hs+360)/2 if hs < 360 else (hs-360)/2
    T = 1 - 0.17*math.cos(math.radians(hb-30)) + 0.24*math.cos(math.radians(2*hb)) + 0.32*math.cos(math.radians(3*hb+6)) - 0.20*math.cos(math.radians(4*hb-63))
    SL = 1 + 0.015*(Lb-50)**2/math.sqrt(20+(Lb-50)**2); SC = 1 + 0.045*Cbp; SH = 1 + 0.015*Cbp*T
    RT = -2*math.sqrt(Cbp**7/(Cbp**7+25**7))*math.sin(math.radians(60*math.exp(-((hb-275)/25)**2)))
    return math.sqrt((dL/SL)**2 + (dC/SC)**2 + (dH/SH)**2 + RT*(dC/SC)*(dH/SH))

if __name__ == '__main__':
    for pair in sys.argv[1:]:
        a, b = pair.split('/')
        print(f'{a} on {b}: {ratio(a, b):.2f}')
