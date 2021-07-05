#!/usr/bin/env python3
import os
import sys
import time
from pdf2image import convert_from_path

import conf_OCR
from pprint import pprint
from conf_OCR import *
from conf_OCR import CreazioneDatabase
from conf_OCR import QueryFir
import sqlite3
import re
import shutil
import cv2
from PIL import Image
import keras_ocr
import random
import string
from itertools import combinations
import numpy as np
import pandas as pd
import pytesseract
import enchant
import time
from nltk import word_tokenize
from pytesseract import Output
import traceback
import logging
from datetime import datetime

now = datetime.now()

PRED_PATH = conf_OCR.PRED_PATH
BASEPATH = conf_OCR.BASEPATH
IMAGE_PATH = conf_OCR.IMAGE_PATH
ARCH_PATH = conf_OCR.ARCH_PATH
PNG_IMAGE_PATH = conf_OCR.PNG_IMAGE_PATH
LOGFILE = conf_OCR.LOGFILE
LOGFILE_ERROR = conf_OCR.LOGFILE_ERROR
TIPOLOGIA_FIR = conf_OCR.TIPOLOGIA_FIR

DPI = conf_OCR.DPI

format = '%(asctime)s : %(name)s : %(levelname)s : %(message)s'
logging.basicConfig(format=format)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
# logging.basicConfig(filename=os.path.join(ARCH_PATH, LOGFILE), filemode='w', format=format)
output_file_handler = logging.FileHandler(os.path.join(ARCH_PATH, LOGFILE), mode='w', encoding='utf-8')
stdout_handler = logging.StreamHandler(sys.stdout)
formatter = logging.Formatter(format)
output_file_handler.setFormatter(formatter)
logger.addHandler(output_file_handler)
# logger.addHandler(stdout_handler)

log_error_path = os.path.join(ARCH_PATH, LOGFILE_ERROR)

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


class GetFileInfo:
    def __init__(self, file='', logger='', web=True):
        self.file = file
        self.db = os.path.join(DB_PATH, 'OCR_MT.db')
        self.conn = sqlite3.connect(self.db)
        self.cur = self.conn.cursor()
        self.web = web
        self.qy = QueryFir(self.web)
        self.logger = logger
        self.file_only = ''
        self.width = None
        self.height = None
        self.crop_width = None
        self.crop_height = None
        self.rotated_file = False
        self.flt = set(['GRAY'])
        self.nome_tipologia = 'NC'
        self.tipologia = 'NC'
        self.produttore = 'NOT FOUND'
        self.trasportatore = 'NOT FOUND'
        self.raccoglitore = 'NOT FOUND'
        self.ocr_fir = {}
        self.full_info = {}

    def word_like_cond(self, target, fieldname='parola', perc=False):
        wcond = {}
        if type(target) is list:
            for word in target:
                word_l = list(word)
                wlike = []
                or_lett = []
                for i, lett in enumerate(word_l):
                    or_lett.append(lett)
                    word_l[i] = '_'
                    if i > 0:
                        word_l[i - 1] = or_lett[i - 1]
                    wlike.append(''.join(word_l))
                wcond[word] = {
                    "{fieldname} like '{perc}{el}{perc}'".format(fieldname=fieldname,
                                                                 el=el, perc='%' if perc else '') for el in wlike
                }
        else:
            word_l = list(target)
            wlike = []
            or_lett = []
            for i, lett in enumerate(word_l):
                or_lett.append(lett)
                word_l[i] = '_'
                if i > 0:
                    word_l[i - 1] = or_lett[i - 1]
                wlike.append(''.join(word_l))
            wcond[target] = {
                "{fieldname} like '{perc}{el}{perc}'".format(fieldname=fieldname,
                                                             el=el, perc='%' if perc else '') for el in wlike
            }

        return wcond

    def get_full_info(self, full_info=''):

        id_fir = self.file_only.split('_')[0]

        q = """
            SELECT * FROM {table}
            WHERE id_fir = '{id_fir}'
        """.format(table='INFO_{}'.format(full_info), id_fir=id_fir)

        item = [row for row in self.cur.execute(q).fetchall()[0]]
        full_info_dict = {
            'id': item[0],
            'id_fir': item[1],
            'a_rag_soc_prod': item[2],
            'a_prov_prod': item[3],
            'a_comune_prod': item[4],
            'a_via_prod': item[5],
            'a_cap_prod': item[6]
        }

        return full_info_dict

    def find_info(self):
        word_like = {}
        if sys.platform == 'win32':
            self.file_only = '_'.join(self.file.split('\\')[-1].split('.')[0].split('_')[:2])
        else:
            self.file_only = '_'.join(self.file.split('/')[-1].split('.')[0].split('_')[:2])

        Image.MAX_IMAGE_PIXELS = 1000000000
        img = Image.open(self.file)
        self.width, self.height = img.size

        self.logger.info('{0} FILE : {1} {0}'.format('+' * 20, self.file_only))

        # CREA DB NEL CASO NON CI FOSSERO TABELLE
        # CREA TABELLE FROM SCRATCH SU HEROKU E FAI PROVE
        CreazioneDatabase(self.db, self.web)
        res = self.check_file()
        # CERCA SE IL FILE E' STATO SALVATO CON ROTAZIONE
        if not res:
            res = self.check_file(rotation=True)
            if res:
                self.rotated_file = True
        if not res:
            self.logger.info('FILE NON TROVATO NEL DB')
            self.logger.info('ANALISI INIZIALE OCR PER FILE {0}'.format(self.file_only))
            self.ocr_analysis(img)
            self.logger.info('FINE ANALISI INIZIALE OCR PER FILE {0}'.format(self.file_only))
        else:
            # SE FILE GIA' REGISTRATO IN DB ALLORA DETERMINO LA TIPOLOGIA A PARTIRE DAL NOME TIPOLOGIA
            for row in res:
                self.nome_tipologia = row[2]
            for key_tipo, val_d in TIPO_FIR.items():
                for key, val in val_d.items():
                    if val == self.nome_tipologia:
                        self.tipologia = key_tipo

        if not self.tipologia == 'NC':
            self.logger.info("PER FILE {0} TIPOLOGIA GIA' INDIVIDUATA --> {1}"
                             .format(self.file_only, self.nome_tipologia))
            if self.rotated_file:
                # SE HO GIA' FIR ANALIZZATO CON ROTAZIONE ALLORA MODIFICO IL FILENAME AGGIUNGENDO DICITURA "_rot"
                orig_filename = self.file_only.split('_rot')[0] + '.png'
                self.logger.info('NOME PRIMA ROTAZIONE {}'.format(orig_filename))
                img = Image.open(os.path.join(PNG_IMAGE_PATH, orig_filename))
                img_copy = img.copy()
                img.close()
                copy_filepath = os.path.join(PNG_IMAGE_PATH, self.file_only + '.png')
                rot = int(self.file_only.split('_rot')[1])
                img_copy = self.rotate_file(img_copy, rot=rot)
                img_copy.save(copy_filepath, 'png')
                os.remove(os.path.join(PNG_IMAGE_PATH, orig_filename))
                # if self.rotated_file:
                #     rot = self.file_only.split('_')[-1]
                #     self.update_rotated_filename(rot)
        else:
            # INDIVIDUO LA TIPOLOGIA DEL FIR ANALIZZATO
            for tipo in TIPO_FIR:
                if tipo == 'NC':
                    continue

                tlist = TIPO_FIR.get(tipo)['TEXT']
                nwlist = []
                for (nword, divy) in TIPO_FIR[tipo]['NO_WORD']:
                    nwlist.append(nword)

                wlist = tlist + nwlist

                word_like[tipo] = self.word_like_cond(wlist)

                self.get_tipologia(tipo, word_like[tipo])
                if not self.tipologia == 'NC':
                    TIPO_FIR[self.tipologia]['FILES'].append(self.file_only)
                    self.aggiorna_campo_tabella(field='tipologia', val_field=self.nome_tipologia)
                    break

            if not os.path.exists(os.path.join(PNG_IMAGE_PATH, "{}".format(self.nome_tipologia))):
                os.makedirs(os.path.join(PNG_IMAGE_PATH, "{}".format(self.nome_tipologia)))

            if self.tipologia == 'NC':
                TIPO_FIR['NC']['FILES'].append(self.file_only)
                # SPOSTO IMMAGINE PNG NELLA CARTELLA "NC"
                img = Image.open(self.file)
                img_copy = img.copy()
                img.close()
                copy_filepath = os.path.join(PNG_IMAGE_PATH, 'NC', self.file_only + '.png')
                img_copy.save(copy_filepath, 'png')

                self.logger.info('TIPOLOGIA NON DETERMINATA PER {} --> '
                                 'NESSUNA INFO A DISPOSIZIONE\n\n\n'.format(self.file_only))
                return

        res = self.check_file(table='OCR_FIR')
        # INSERISCO PAROLE DA ACCETTARE QUALORA VENISSERO INDIVIDUATE DURANTE OCR
        # self.insert_common_words(info_fir='PRODUTTORE')
        # self.get_full_info(full_info='PRODUTTORE')
        self.full_info = self.read_full_info(info='PRODUTTORI')
        if res:
            self.logger.info("INFO GIA' ACQUISITE. ESECUZIONE PER FILE {} TERMINATA".format(self.file_only))
            return self.ocr_fir
        # IN CASO DI TIPOLOGIA TROVATA E NON ANALIZZATA, SI CERCANO LE INFO
        for inf in ['prod', 'trasp', 'racc']:
            self.get_info_fir(inf)
            break

        return self.ocr_fir

    def esclusione_parole_tipologia(self, tipo, word_like, pid):

        for (nword, divy) in TIPO_FIR[tipo]['NO_WORD']:
            # RICERCA PAROLA DA ESCLUDERE VICINA A QUELLE CERCATE
            clike = '(' + ' or '.join(word_like[nword]) + ')'
            if len(nword) > 10:
                plike = """
                    ( parola like '{s00}%{s01}' OR
                    parola like '%{s10}' OR
                    parola like '{s20}%')
                """.format(s00=nword[:3], s01=nword[-3:], s10=nword[-8:], s20=nword[5:])
            else:
                plike = """
                    ( parola like '{s00}%{s01}')
                """.format(s00=nword[:3], s01=nword[-3:])

            nowq = """
                {sub_body}
                WHERE
                ({clike} OR {plike} ) AND
                p.id < {pid} + {did} AND
                p.id > {pid} - {did} AND
                file = '{file}';
            """.format(sub_body=self.qy.sub_body, clike=clike, plike=plike,
                       pid=pid, did=TIPO_FIR[tipo]['DLT_ID'], file=self.file_only)

            nwres = self.cur.execute(nowq).fetchall()
            if nwres:
                self.logger.info('TROVA PAROLA INTRUSA PER QUERY: \n{}'.format(nowq))
                return nwres

        # PAROLA LONTANA DA QUELLE CERCATE (ES. "ROTTAMI") MA CHE,
        # SE TROVATA, ESCLUDE LA TIPOLOGIA (ES. "TIPOLOGIA A")
        # ---- LA PAROLA INTRUSA PUO' TROVARSI IN UN QUADRANTE DIVERSO (DEFINITO DA divy) ----
        nowords = [str(w) for (w, divy) in TIPO_FIR[tipo]['NO_WORD']]
        exc_word = "('" + "','".join(nowords) + "')"
        nowq = """
            {sub_body}
            WHERE
            (parola in {exc_word}) AND
            div_x = '1-2' AND
            div_y = '{divy}' AND
            file = '{file}';
        """.format(sub_body=self.qy.sub_body, exc_word=exc_word, divy=divy, file=self.file_only)

        nwres = self.cur.execute(nowq).fetchall()
        if nwres:
            self.logger.info('ESCLUSIONE CON PAROLA LONTANA {}'.format(nowq))
            return nwres

        return None

    def get_tipologia(self, tipo, word_like):

        occ_l = []

        for ii, txt in enumerate(TIPO_FIR[tipo]['TEXT']):
            if not ii == len(TIPO_FIR[tipo]['TEXT']) - 1:
                txt = TIPO_FIR[tipo]['TEXT'][0]
                clike = '(' + ' or '.join(word_like[txt]) + ')'
                plike = """
                    ( parola like '{s00}%{s01}' OR
                    parola like '{s00}%' OR
                    parola like '%{s01}' )
                """.format(s00=txt[:3], s01=txt[-3:])

                subq = """
                    {sub_body} WHERE
                    ({clike} OR {plike}) AND
                    div_x = '1-2' AND
                    div_y = '1-4' AND
                    file = '{file}'
                    LIMIT 1;
                """.format(sub_body=self.qy.sub_body, clike=clike, plike=plike, file=self.file_only)

                self.logger.debug('RICERCA {0} : {1}'.format(tipo.upper(), subq))
                sres = self.cur.execute(subq).fetchall()

                if not sres:
                    self.logger.info(
                        "Nessun risultato idoneo per la parola {0}. "
                        "Il file {1} non appartiene alla tipologia {2}".format(txt, self.file_only, tipo))
                    return

                pid = sres[0][0]

                if TIPO_FIR[tipo]['NO_WORD']:
                    # RICERCA FALSI POSITIVI. SE HO UN RISCONTRO ESCLUDO LA TIPOLOGIA
                    nwres = self.esclusione_parole_tipologia(tipo, word_like, pid)
                    if nwres:
                        self.logger.info('Trovata parola che esclude il file {0} dalla tipologia {1}'
                                         .format(self.file_only, tipo))
                        return

            else:
                clike = '(' + ' or '.join(word_like[txt]) + ')'
                plike = """
                    (parola like '{s00}%{s01}' OR
                    parola like '%{s10}%' OR
                    parola like '{s20}%')
                """.format(s00=txt[:2], s01=txt[-2:], s10=txt[2:-2], s20=txt[:3])

                q = """
                    {body}
                    WHERE
                    ({clike} OR {plike}) AND
                    p.id < {pid} + {did} AND
                    p.id > {pid} - {did} AND
                    file = '{file}';
                """.format(body=self.qy.body, clike=clike, plike=plike,
                           pid=pid, did=TIPO_FIR[tipo]['DLT_ID'], file=self.file_only)

                self.logger.debug('RICERCA {0} : {1}'.format(tipo.upper(), q))
                res = self.cur.execute(q).fetchall()

                occ_l.append(len(res))

                if occ_l == [0 * i for i in range(len(occ_l))]:
                    self.logger.info("Nessun risultato idoneo per la parola {0}. "
                                     "Il file {1} non appartiene alla tipologia {2}"
                                     .format(txt, self.file_only, tipo))
                    return

                self.tipologia = tipo
                self.nome_tipologia = TIPO_FIR['{}'.format(self.tipologia)]['NAME']
                self.logger.info('{0} TIPOLOGIA FIR : {1} {0}'.format('-' * 20, self.nome_tipologia))

    def image_preprocessing(self, cfilepath):
        # PREPROCESSING
        im = cv2.imread(cfilepath)
        # SOVRASCRIVO RITAGLIO CON IMMAGINE GRIGIA
        # gray = cv2.cvtColor(np.uint8(im), cv2.COLOR_BGR2GRAY)
        gray = self.get_grayscale(np.uint8(im))
        if self.nome_tipologia == 'FIR - TRS':
            self.flt.add('THRS')
            self.flt.add('BLUR_GAUSS')
            gray = self.thresholding(gray)
            # TYPE = MEDIAN PUO' DARE UN RISULTATO MIGLIORE
            # VERIFICA IN FUTURO SE VUOI ACCEDERE A QUESTO FITRO PER OCR
            gray = self.remove_noise(gray, type='gaussian')
        # NUOVA SOGLIA IMMAGINE, PIXELS IN RISALTO A 255 MENTRE QUELLI IN BACKGROUN VANNO A 0
        # gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
        # RIMUOVO IL RUMORE
        # gray = cv2.medianBlur(gray, 3)
        return gray

    def ocr_analysis_ritaglio(self, info, cutoff_width=0, config_ocr=r'--oem 3 --psm 4'):
        info_fir = INFO_FIR[info.upper()]['TEXT']
        # PROVATO https://www.geeksforgeeks.org/text-detection-and-extraction-using-opencv-and-ocr/
        # ALTRO MODO https://arxiv.org/ftp/arxiv/papers/1509/1509.03456.pdf
        # USATO https://www.pyimagesearch.com/2017/07/10/using-tesseract-ocr-python/

        # PER INFO PYTESSERACT
        # https://jaafarbenabderrazak-info.medium.com/ocr-with-tesseract-opencv-and-python-d2c4ec097866
        # BEST PRACTICES https://ai-facets.org/tesseract-ocr-best-practices/
        orig_filepath = os.path.join(PNG_IMAGE_PATH, '{0}.png'.format(self.file_only))
        # SE HO FILENAME MODIFICATO CON "_rot" ALLORA CONSIDERO IL FILE ORIGINALE SENZA QUESTA DICITURA
        # PER EFFETTUARE IL RITAGLIO
        # if self.rotated_file:
        #     orig_filepath = os.path.join(PNG_IMAGE_PATH, '{0}.png'.format(self.file_only))
        img = Image.open(orig_filepath)
        left = TIPO_FIR['{}'.format(self.tipologia)]['SIZE_OCR'][0]
        top = TIPO_FIR['{}'.format(self.tipologia)]['SIZE_OCR'][1]

        if not self.rotated_file:
            right = self.width + 5 + cutoff_width  # SCELGO UN RITAGLIO PER TUTTA
            # LA LARGHEZZA DEL FIR + 5 (SCELTA CHE PORTA SOLO A MIGLIORARE OCR) + CUTOFF INSERITO
        else:
            # LA ROTAZIONE CONSIDERA UNA MAGGIORE ESTENSIONE DEL FOGLIO IN LARGHEZZA CHE NON E' OTTIMALE
            # DIMINUISCO DI 1000 IN LARGHEZZA
            top = TIPO_FIR['{}'.format(self.tipologia)]['SIZE_OCR'][1] + 300
            right = self.width - 1000 + 5 + cutoff_width

        # PER FIR - TRS ALTEZZA RITAGLIO PROD E' NEL RANGE [650,1050] per SIZE STANDARD INPUT FIR di 3334
        # MANTENGO RAPPORTO 650 / 3334 = 0.1949.. NEL CASO DI ALTEZZE DIVERSE IN INPUT
        # MANTENGO RAPPORTO 1050 / 3334 = 0.3149.. NEL CASO DI ALTEZZE DIVERSE IN INPUT

        if self.height < 3360:
            # LA ROTAZIONE CONSIDERA UNA MAGGIORE ESTENSIONE DEL FOGLIO IN ALTEZZA CHE NON E' OTTIMALE
            # AGGIUNGO 300 IN ALTEZZA
            if self.rotated_file:
                top = TIPO_FIR['{}'.format(self.tipologia)]['SIZE_OCR'][1] + 300
            else:
                top = TIPO_FIR['{}'.format(self.tipologia)]['SIZE_OCR'][1]
            bottom = TIPO_FIR['{}'.format(self.tipologia)]['SIZE_OCR'][3]
        else:
            if self.nome_tipologia == 'FORMULARIO RIFIUTI - ALLEGATO B - ETM':
                # LA ROTAZIONE CONSIDERA UNA MAGGIORE ESTENSIONE DEL FOGLIO IN ALTEZZA CHE NON E' OTTIMALE
                # AGGIUNGO 300 IN ALTEZZA
                if self.rotated_file:
                    top = 0 + 300
                else:
                    top = 0
                bottom = (self.height * 0.2099)
            elif self.nome_tipologia == 'FIR - TRS':
                top = (self.height * 0.1949)
                bottom = (self.height * 0.3149)

        wsize = int(right) - int(left)
        hsize = int(bottom) - int(top)

        self.logger.info('ANALISI OCR RITAGLIO {0} PER FILE {1} : SIZE ( {2} - {3} )'
                         .format(INFO_FIR[info.upper()]['TEXT'], self.file_only, wsize, hsize))

        img_copy = img.copy()
        img.close()

        copy_filepath = os.path.join(PNG_IMAGE_PATH, '{}_copy.png'.format(self.file_only))

        img_copy.save(copy_filepath, 'png')

        img_copy = Image.open(copy_filepath)

        img_crop = img_copy.crop((left, top, right, bottom))
        self.crop_width, self.crop_height = img_crop.size

        cfilename = '{0}_{1}.png'.format(self.file_only, INFO_FIR[info.upper()]['TEXT'])
        cfilepath = os.path.join(PNG_IMAGE_PATH, '{}'.format(self.nome_tipologia), cfilename)
        img_crop.save(cfilepath)

        gray = self.image_preprocessing(cfilepath)
        # im = cv2.imread(cfilepath)
        # gray = cv2.cvtColor(np.uint8(im), cv2.COLOR_BGR2GRAY)

        cv2.imwrite("{}".format(cfilepath), gray)
        # RIMUOVO COPIA E FILE INTERO. MANTENGO SOLO IL RITAGLIO
        os.remove(copy_filepath)

        img_crop = Image.open(cfilepath)
        # d = pytesseract.image_to_data(img_crop, output_type=Output.DICT)
        text = pytesseract.image_to_string(img_crop, config=config_ocr)
        # self.logger.info('DICT {}'.format(d['text']))
        text_l = text.split('\n')
        data = {'{}'.format(info_fir): []}
        foo = []
        for txt in text_l:
            if txt:
                foo.append(txt.split(' '))

        for t_l in foo:
            for t in t_l:
                if re.search("\w", t):
                    data.get(info_fir).append(t)

        self.logger.info('data {}'.format(data))
        self.logger.info('{0} {1} RECORDS TROVATI {0}'.format('-' * 20, len(data.get(info_fir))))

        if not data.get(info_fir):
            return None, None, None

        parole, id_st, id_fin = self.query_info_db(data)

        self.logger.info('FINE ANALISI OCR RITAGLIO {0} PER FILE {1}'
                         .format(INFO_FIR[info.upper()]['TEXT'], self.file_only))

        return parole, id_st, id_fin

    def update_rotated_filename(self, rot):
        orig_file = self.file
        orig_file_only = self.file_only
        self.logger.info('FILE NAME PRIMA ROTAZIONE {}'.format(self.file_only))
        self.file = self.file.split('.png')[0] + '_rot{}'.format(rot) + '.png'
        # split('_')[3] poichè considero stringa aggiuntiva '_rot'
        if sys.platform == 'win32':
            self.file_only = '_'.join(self.file.split('\\')[-1].split('.')[0].split('_')[:3])
        else:
            self.file_only = '_'.join(self.file.split('/')[-1].split('.')[0].split('_')[:3])
        self.logger.info('NUOVO NOME DOPO ROTAZIONE : {}'.format(self.file_only))
        q = """
            UPDATE files_WEB SET file = '{rot_file}'
            WHERE file = '{orig_file}'
        """.format(rot_file=self.file_only, orig_file=orig_file_only)
        self.cur.execute(q)
        self.conn.commit()
        os.remove(os.path.join(orig_file))

    def ocr_analysis(self, img):
        # SEE https://github.com/faustomorales/keras-ocr/issues/65
        # Disable GPU, use CPU only
        os.environ['CUDA_VISIBLE_DEVICES'] = '-1'
        # alphabet = string.digits + string.ascii_letters + '!?. '
        # recognizer_alphabet = ''.join(sorted(set(alphabet.lower())))
        # recognizer = keras_ocr.recognition.Recognizer(
        #     alphabet=recognizer_alphabet,
        #     weights='kurapan'
        # )
        # recognizer.model.load_weights(os.path.join(PRED_PATH, 'Tuning_Recognizer', 'recognizer_borndigital.h5'))
        # pipeline = keras_ocr.pipeline.Pipeline(recognizer=recognizer)
        pipeline = keras_ocr.pipeline.Pipeline()
        # PROVO INIZIALMENTE OCR DEL FILE SENZA ROTAZIONE
        # SE HO RISULTATO CORRETTO ESCO DAL CICLO FOR
        for rot in [0, 90, -90]:
            img = self.rotate_file(img, rot=rot)
            # CONSIDERO I RITAGLI (lunghezza / nw, altezza / nh)
            nw = 2
            nh = 4
            data = []
            # CONSIDERO SOLO I PRIMI DUE RITAGLI SUPERIORI
            self.logger.info("{0} ESECUZIONE OCR PER FILE {1} {0}".format('-' * 20, self.file_only))
            for ih in [1, 2]:
                top = (ih - 1) * self.height / nh
                bottom = ih * self.height / nh
                div_y = "{:.3f}H".format(float(ih / nh))
                # CONSIDERO SOLO IL PRIMO RITAGLIO DA SINISTRA
                for iw in [1]:
                    image = []
                    left = (iw - 1) * self.width / nw
                    right = iw * self.width / nw
                    img_crop = img.crop((left, top, right, bottom))
                    div_x = "{:.3f}W".format(float(iw / nw))
                    cfilepath = os.path.join(IMAGE_PATH, 'CROP_{0}_{1}-{2}.png'.format(self.file_only, div_x, div_y))
                    img_crop.save("{}".format(cfilepath), 'png')
                    image.append(keras_ocr.tools.read(cfilepath))
                    self.logger.info("{0} INIZIO PER RITAGLIO {1}-{2} {0}".format('-' * 20, div_x, div_y))
                    tstart = time.time()
                    # OGNI ELEMENTO OCR E' UNA LISTA DI TUPLE (parola, coordinate_parola)
                    raw_data = pipeline.recognize(image)
                    self.logger.info('{0} FINE ESECUZIONE IN {1} SECONDI {0}'.format('-' * 20, time.time() - tstart))
                    os.remove(cfilepath)

                    for (t, c) in raw_data[0]:
                        data.append((t, c.tolist(), '{}-{}'.format(iw, nw), '{}-{}'.format(ih, nh),))

            # self.logger.info('DATA {}'.format(pprint.pprint(data)))
            self.logger.info('{0} FINE ESECUZIONE OCR {0}'.format('-' * 20))
            self.insert_new_records_table(table='files_WEB', dpi=DPI)
            self.insert_new_records_table(data=data, table='parole_WEB', dpi=DPI)

            # CHECK SE OCR RISULTA INADEGUATO POICHE' IL FILE E' RUOTATO
            res = self.check_file(table='parole_WEB', rotation=True)
            for row in res:
                tilted_rows = row[0]
            perc_tilted_rows = int(tilted_rows / len(data) * 100)
            self.logger.info('PERCENTUALE RIGHE SOSPETTE (LUNGHEZZA 0 OPPURE 1) : {}%'.format(perc_tilted_rows))

            # SE LA PERCENTUALE DI RIGHE NON ACCETTATE E' INFERIORE AL 60% ALLORA ACCETTO IL RISULTATO
            if not perc_tilted_rows > 60:
                if self.rotated_file:
                    self.update_rotated_filename(rot)
                    self.logger.info('OCR INIZIALE FILE {} VALIDO CON ROTAZIONE {}'
                                     .format(self.file_only, rot))
                else:
                    self.logger.info('OCR INIZIALE FILE {} VALIDO CON ROTAZIONE NULLA'
                                     .format(self.file_only))
                img.save(self.file)
                break

    def check_file(self, table='files_WEB', rotation=False):
        data_info = None
        if table == 'OCR_FIR':
            q = """
                SELECT *
                FROM {table}
                WHERE file = '{file}'
            """.format(table=table, file=self.file_only)
        elif table == 'files_WEB':
            if rotation:
                q = """
                    SELECT *
                    FROM {table}
                    WHERE file like '{file}_rot%'
                    AND
                    tipologia != 'NC'
                """.format(table='files_WEB' if self.web else 'files', file=self.file_only)
            else:
                q = """
                    SELECT *
                    FROM {table}
                    WHERE file = '{file}'
                    AND
                    tipologia != 'NC'
                """.format(table='files_WEB' if self.web else 'files', file=self.file_only)
        elif table == 'parole_WEB':
            if rotation:
                q = """
                    SELECT count(parola) FROM parole_WEB p
                    LEFT JOIN files_WEB f 
                    ON (f.id=p.id_file)
                    WHERE file = '{file}' AND 
                    length(parola) in (0, 1);
                """.format(file=self.file_only)
        else:
            q = """
               SELECT *
               FROM {table1} t1
               LEFT JOIN {table2} t2
               ON (t1.id_file=t2.id)
               WHERE file = '{file}'
           """.format(table1=table, table2='files_WEB' if self.web else 'files',
                      file=self.file_only)
        res = self.cur.execute(q).fetchall()
        if table == 'files_WEB':
            row = [elem for elem in res]
            try:
                self.file_only = row[0][1]
                self.file = os.path.join(PNG_IMAGE_PATH, self.file_only + '.png')
            except Exception:
                self.logger.info('RISULTATO NON TROVATO NEL DB CON ROTAZIONE = {}'.format(rotation))
        if table == 'OCR_FIR' and res:
            item = res[0]
            self.ocr_fir = {'ocr_prod': item[4], 'ocr_trasp': item[5], 'ocr_racc': item[6], 'ocr_size': item[2]}

        return res

    def query_info_db(self, data):
        q = """
            SELECT id FROM files_WEB
            WHERE file = "{file}"
        """.format(file=self.file_only)

        id_file = self.cur.execute(q).fetchall()[0][0]

        info_fir = None
        for key in data.keys():
            info_fir = key

        res = self.check_file(table='OCR_{}'.format(info_fir))

        if not res:
            for par in data.get(info_fir):
                # ELIMINO CARATTERE UNDERSCORE (POICHE' E' ALFANUMERICO)
                par = re.sub('_', '', par)
                if par.lower() in ['s.r.l', 'sr.l', 's.rl']:
                    res_par = 'srl'
                # SE TROVO CARATTERI SPECIALI NELLA PAROLA
                elif re.search('\W', par) and len(par) > 3:
                    res_par = re.split('\W', par)
                    for rpar in res_par:
                        # DEVO SEPARARE CARATTERI DA CIFRE ES. AUTORICAMBI3.GI
                        if re.search('\d', rpar):
                            res_par = re.split('\d', rpar)
                # SE TROVO CARATTERI ORDINALI INSIEME A CIFRE
                elif re.search('\w', par) and re.search('\d', par) and len(par) > 3:
                    res_par = re.split('\d', par)
                # if par == 'PRODUTTORE/DETENTORE':
                #     res_par = par.split('/')
                else:
                    res_par = ''
                    for c in par:
                        if c in string.printable:
                            res_par += c
                if isinstance(res_par, str):
                    # CASO OCR (CON PYTESSERACT POSSO OTTENERE CARATTERI INTRUSI ES. '‘PRODUTTORE')
                    res_par = re.sub('\W', '', res_par)
                    # res_par = re.sub('[\[()‘“~/?+"\'_.=~\-\]]', '', res_par)
                    if res_par:
                        q = """
                            INSERT INTO {table}(parola,id_file,flt,ts)
                            VALUES ("{par}", "{id_file}", "{flt}", CURRENT_TIMESTAMP)
                        """.format(table='OCR_{}'.format(info_fir), par=res_par, id_file=id_file, flt=self.flt)
                        self.cur.execute(q)
                        self.conn.commit()
                elif isinstance(res_par, list):  # CASO 'PRODUTTORE/DETENTORE'
                    for rpar in res_par:
                        rpar = re.sub('_', '', rpar)
                        if rpar:
                            q = """
                                INSERT INTO {table}(parola,id_file,flt,ts)
                                VALUES ("{par}", "{id_file}", "{flt}", CURRENT_TIMESTAMP)
                            """.format(table='OCR_{}'.format(info_fir), par=rpar, id_file=id_file, flt=self.flt)
                            self.cur.execute(q)
                            self.conn.commit()
        else:
            self.logger.info("RITAGLIO GIA' ANALIZZATO DA OCR")
            self.logger.info('NESSUN INSERIMENTO IN TABELLA OCR_{} PER FILE {}'.format(info_fir, self.file_only))

        q = """
            SELECT parola FROM {table}
            WHERE id_file = '{id_file}'
        """.format(table='OCR_{}'.format(info_fir), id_file=id_file)

        parole = []
        for par in self.cur.execute(q).fetchall():
            parole.append(par[0])

        q = """
            SELECT id FROM {table}
            WHERE id_file = '{id_file}'
            ORDER BY id ASC
            LIMIT 1
        """.format(table='OCR_{}'.format(info_fir), id_file=id_file)

        id_st = self.cur.execute(q).fetchall()[0][0]

        q = """
            SELECT id FROM {table}
            WHERE id_file = '{id_file}'
            ORDER BY id DESC
            LIMIT 1
        """.format(table='OCR_{}'.format(info_fir), id_file=id_file)

        id_fin = self.cur.execute(q).fetchall()[0][0]

        return parole, id_st, id_fin

    def insert_info_db(self, data):
        q = """
            INSERT INTO OCR_FIR (file,ocr_size, flt,ocr_prod,ocr_trasp,ocr_racc,ts)
                VALUES ("{file}", "{ocr_size}", "{flt}", "{ocr_prod}", "{ocr_trasp}", "{ocr_racc}", CURRENT_TIMESTAMP)
            """.format(file=self.file_only, ocr_size=data['ocr_size'], flt=self.flt, ocr_prod=data['ocr_prod'],
                       ocr_trasp=data['ocr_trasp'], ocr_racc=data['ocr_racc'])

        self.cur.execute(q)
        self.conn.commit()

    def insert_new_records_table(self, data=[], table='files_WEB', dpi=200, flt=''):
        if self.web:
            q = """
                SELECT * FROM files_WEB WHERE file = '{file}'
            """.format(file=self.file_only)
            # CONTROLLA SE FILE E' GIA' STATO CONSIDERATO
            res = self.cur.execute(q).fetchall()

            q = 'SELECT id FROM files_WEB ORDER BY id DESC LIMIT 1'

            if self.cur.execute(q).fetchall():
                last_id = self.cur.execute(q).fetchall()[0][0]
            else:
                last_id = 0

            new_id = last_id + 1

            if table == 'files_WEB' and not res:
                q = """
                    INSERT INTO files_WEB (id,file,tipologia,produttore,trasportatore,raccoglitore,ts)
                    VALUES ('{id}', '{file}', '{tipologia}', '', '', '', CURRENT_TIMESTAMP)
                """.format(id=new_id, file=self.file_only, tipologia=self.nome_tipologia)
                self.cur.execute(q)

            elif table == 'parole_WEB':
                # CANCELLO PRECEDENTE OCR NON OTTIMALE
                self.delete_table(table='parole_WEB')
                self.logger.info('INSERIMENTO DI {0} RECORDS PER FILE {1}'.format(len(data), self.file_only))
                # NUOVO INSERIMENTO OCR
                for par, (lu, ru, ld, rd), div_x, div_y in data:
                    q = """
                        INSERT INTO parole_WEB (parola, coor_x, coor_y, id_file, div_x, div_y, dpi, flt, ts)
                        VALUES
                            ("{0}", "{1}", "{2}", "{3}", "{4}", "{5}", "{6}", "{7}", CURRENT_TIMESTAMP)
                    """.format(par, lu[0], lu[1], last_id, div_x, div_y, dpi, flt)
                    self.cur.execute(q)

        self.conn.commit()

    def aggiorna_campo_tabella(self, field='', val_field=''):

        q = """
            UPDATE "{table}"
            SET "{field}" = "{val_field}"
            WHERE file = "{file}"
        """.format(table='files_WEB' if self.web else 'files', field=field,
                   val_field=val_field, file=self.file_only)

        self.cur.execute(q)
        self.conn.commit()

    def get_delim_words(self, info, btw_words, id_st, id_fin):
        delim_words = {}
        len_dw_st = 0

        for ii, words_lst in enumerate(btw_words):
            word_like = self.word_like_cond(words_lst)
            if ii == 1:
                # NUMERO PAROLE INIZIALI INTERCETTATE
                len_dw_st = len(delim_words)
            for jj, txt in enumerate(words_lst):
                clike = '(' + ' or '.join(word_like[txt]) + ')'
                # INSERITO ORDER BY DESC PER OCR CON CARATTERI INTRUSI (ES. '‘PRODUTTORE')
                q = """
                    SELECT t1.id, parola
                    FROM {table} t1
                    LEFT JOIN files_WEB t2
                    ON (t1.id_file=t2.id)
                    WHERE
                    file = '{file}' AND
                    {clike}
                    ORDER BY parola {ord}
                    LIMIT 1;
                """.format(table='OCR_{}'.format(INFO_FIR[info.upper()]['TEXT']),
                           file=self.file_only, clike=clike, ord='DESC')

                if self.cur.execute(q).fetchall():
                    delim_words[txt] = [(item[0], item[1], '{}'.format('ALTO' if ii == 0 else 'BASSO'))
                                        for item in self.cur.execute(q).fetchall()]
                elif (not self.cur.execute(q).fetchall()) and (ii == 0) \
                        and (jj == len(btw_words[0]) - 1) and (len(delim_words) == 0):
                    # SE NON TROVO ALCUNA PAROLA CHE IDENTIFICA INIZIO RITAGLIO
                    delim_words['START_INFO'] = [(id_st, 'NO WORD', 'EOF')]
                elif (not self.cur.execute(q).fetchall()) and (ii == 1) \
                        and (jj == len(btw_words[1]) - 1) and (len(delim_words) - len_dw_st == 0):
                    # SE NON TROVO ALCUNA PAROLA CHE IDENTIFICA FINE RITAGLIO
                    delim_words['END_INFO'] = [(id_fin, 'NO WORD', 'EOF')]

        self.logger.info('PAROLE CHIAVE CHE DELIMITANO INFO {}'.format(delim_words))
        return delim_words

    def get_info_fir(self, info):
        id_coor = {}

        # CERCO TRA PAROLA INIZIALE E FINALE PER RICERCA INTERNA DELLA INFO CERCATA
        btw_words = [INFO_FIR[info.upper()]['BTWN_WORD']['INIZ'], INFO_FIR[info.upper()]['BTWN_WORD']['FIN']]

        for ii, words_lst in enumerate(btw_words):

            word_like = self.word_like_cond(words_lst)
            self.logger.info('BTWN WORDS {}'.format(words_lst))
            for txt in words_lst:
                clike = '(' + ' or '.join(word_like[txt]) + ')'
                subq = """
                    {sub_body} WHERE
                    file = '{file}' AND
                    {clike} AND
                    div_y in ({divy})
                    ORDER BY p.id {ord}
                    LIMIT 1;
                """.format(sub_body=self.qy.sub_body, file=self.file_only, clike=clike,
                           divy="'1-4', '2-4'" if (info in ('trasp', 'racc')) or self.rotated_file else "'1-4'",
                           ord='ASC' if info == 'prod' else 'DESC')

                self.logger.debug('FILE {0} RICERCA BASE {1} : {2}'
                                  .format(self.file_only, INFO_FIR[info.upper()]['TEXT'], subq))
                # se parola like dà risultato esco subito
                res = self.cur.execute(subq).fetchall()
                if res:
                    for r in res:
                        id_coor[txt] = (r, '{}'.format('ALTO' if ii == 0 else 'BASSO'))
                        self.logger.info('RISULTATO : {}'.format(id_coor))
                    break
                # avendo parola di tanti caratteri provo a fare like con %
                if len(txt) > 7:
                    plike = 'OR ( parola like "%{s0}%" or parola like "%{s1}%")'.format(s0=txt[:5], s1=txt[-5:])
                    subq = """
                       {sub_body} WHERE
                       file = '{file}' AND
                       ({clike} {plike})
                       ORDER BY p.id ASC
                       LIMIT 1;
                    """.format(sub_body=self.qy.sub_body, file=self.file_only, clike=clike, plike=plike)

                    self.logger.debug('RICERCA PAROLA LUNGA {0} : {1}'.format(INFO_FIR[info.upper()]['TEXT'], subq))
                    res = self.cur.execute(subq).fetchall()
                    if res:
                        for r in res:
                            id_coor[txt] = (r, '{}'.format('ALTO' if ii == 0 else 'BASSO'))
                        break

            if (len(id_coor) == 0 and ii == 0) or (len(id_coor) == 1 and ii == 1):
                self.logger.error('RICERCA ZONA OCR NON CORRETTA')
                try:
                    self.logger.error('TROVATA SOLO PAROLA ZONA INTERMEDIA {}'
                                      .format([v[1] for v in id_coor.values()][0]))
                except Exception:
                    self.logger.info('LISTA VUOTA --> NESSUNA PAROLA TROVATA')
                # SPOSTO IMMAGINE PNG NELLA CARTELLA TIPOLOGIA ASSOCIATA
                img = Image.open(self.file)
                img_copy = img.copy()
                img.close()
                copy_filepath = os.path.join(PNG_IMAGE_PATH, self.nome_tipologia, self.file_only + '.png')
                img_copy.save(copy_filepath, 'png')
                return

        self.logger.info('PAROLE CHE DETERMINANO OCR PER ZONA {0} :\n{1}'
                         .format(INFO_FIR[info.upper()]['TEXT'], id_coor))

        self.logger.info('{0} PRIMO TENTATIVO CREAZIONE RITAGLIO OCR {1} PER FILE {2} {0}'
                         .format('+' * 20, INFO_FIR[info.upper()]['TEXT'], self.file_only))

        words, id_st, id_fin = self.ocr_analysis_ritaglio(info)

        if not words:
            self.logger.info('ANALISI OCR NON HA INDIVIDUATO ALCUNA PAROLA. '
                             'ESECUZIONE FILE {} TERMINATA'.format(self.file_only))
            return

        self.logger.info('\n{0} RICERCA RITAGLIO {1} {0}\n'.format('#' * 20, INFO_FIR[info.upper()]['TEXT']))
        self.logger.info('RANGE PAROLE INDIVIDUATE {}'.format(words))

        delim_words = self.get_delim_words(info, btw_words, id_st, id_fin)

        self.check_ritaglio(delim_words, info)

        NTENTATIVI = 4

        # SECONDO TENTATIVO OCR RITAGLIO --> ALLARGO RITAGLIO E RIPROVO
        if not self.ocr_fir:
            for itentativo in range(NTENTATIVI):
                # CAMBIO SIZE DEL RITAGLIO
                self.delete_table(table='ocr', info_fir=INFO_FIR[info.upper()]['TEXT'])
                self.logger.info('{0} TENTATIVO NO. {1} PER CREAZIONE RITAGLIO OCR {2} PER FILE {3} {0}'
                                 .format('+' * 20, itentativo + 2, INFO_FIR[info.upper()]['TEXT'], self.file_only))

                # PER MAC OCR TESSERACT -->  'PROD': [0, 0, self.width + 10, 700]
                # PER WIN OCR TESSERACT -->  'PROD': [0, 0, self.width, self.height / 3]
                if itentativo % 2 == 0:
                    itime = 1
                    molt = itentativo // 2
                else:
                    itime = - 1
                    molt = itentativo // 2

                cutoff_width = ((molt + 1) * itime * 5)

                words, id_st, id_fin = self.ocr_analysis_ritaglio(info, cutoff_width=cutoff_width)

                if not words:
                    self.logger.info('ANALISI OCR NON HA INDIVIDUATO ALCUNA PAROLA. '
                                     'ESECUZIONE FILE {} TERMINATA'.format(self.file_only))
                    return

                self.logger.info('\n{0} RICERCA RITAGLIO {1} {0}\n'.format('#' * 20, INFO_FIR[info.upper()]['TEXT']))
                self.logger.info('RANGE PAROLE INDIVIDUATE {}'.format(words))

                delim_words = self.get_delim_words(info, btw_words, id_st, id_fin)

                self.check_ritaglio(delim_words, info)
                # SE TROVO RISULTATO ESCO
                if self.ocr_fir:
                    break

        if not self.ocr_fir:
            # NEL CASO NON ABBIA RISULTATI CAMBIO PARAMETRI OCR DI PYTESSEACT (--oem 3 --psm 6)  E RIPETO
            for itent in range(2):
                self.delete_table(table='ocr', info_fir=INFO_FIR[info.upper()]['TEXT'])
                self.logger.info('{0} TENTATIVO NO. {1} CREAZIONE RITAGLIO OCR {2} PER FILE {3} '
                                 'CONFIG OCR DIVERSO {0}'
                                 .format('+' * 20, itent + 2, INFO_FIR[info.upper()]['TEXT'], self.file_only))
                if itent % 2 == 0:
                    itime = 1
                    molt = itent // 2
                else:
                    itime = - 1
                    molt = itent // 2

                cutoff_width = ((molt + 1) * itime * 5)
                words, id_st, id_fin = self.ocr_analysis_ritaglio(info, cutoff_width=cutoff_width,
                                                                  config_ocr=r'--oem 3 --psm 6')

                if not words:
                    self.logger.info('ANALISI OCR NON HA INDIVIDUATO ALCUNA PAROLA. '
                                     'ESECUZIONE FILE {} TERMINATA'.format(self.file_only))
                    return

                self.logger.info('\n{0} RICERCA RITAGLIO {1} {0}\n'
                                 .format('#' * 20, INFO_FIR[info.upper()]['TEXT']))
                self.logger.info('RANGE PAROLE INDIVIDUATE {}'.format(words))

                delim_words = self.get_delim_words(info, btw_words, id_st, id_fin)

                self.check_ritaglio(delim_words, info)
                # SE TROVO RISULTATO ESCO
                if self.ocr_fir:
                    break

        if self.ocr_fir:
            self.logger.info('INSERIMENTO IN TABELLA OCR_{}'.format(INFO_FIR[info.upper()]['TEXT']))
            self.insert_info_db(self.ocr_fir)

    def check_ocr_files(self, info_ocr=None):
        q = """
            SELECT DISTINCT file 
            FROM "{table}"
        """.format(table='files_WEB' if self.web else 'files')

        files_lst = []
        for row in self.cur.execute(q).fetchall():
            files_lst.append(row[0])

        tot_files = "'" + "','".join(files_lst) + "'"

        q = """
            SELECT DISTINCT file 
            FROM "{table}"
            WHERE file in ({tot_files})
        """.format(table='OCR_FIR', tot_files=tot_files)

        ocr_files_lst = []
        for row in self.cur.execute(q).fetchall():
            ocr_files_lst.append(row[0])

        self.logger.info('{0} {1} FILES ANALIZZATI PER OCR {2} {0}'
                         .format('+' * 20, len(ocr_files_lst), INFO_FIR[info_ocr.upper()]['TEXT']))

        tot_missed_ocr = len(files_lst) - len(ocr_files_lst)
        if not (tot_missed_ocr == 0):
            self.logger.info('{0} {1} FILES MANCANTI PER OCR {2} {0}'
                             .format('!' * 20, tot_missed_ocr, INFO_FIR[info_ocr.upper()]['TEXT']))
            missed_ocr = set(files_lst) - set(ocr_files_lst)
            self.logger.info('OCR MANCANTE PER I SEGUENTI FILES: {}'.format(missed_ocr))

    def read_full_info(self, info=''):
        full_info_dict = {
            'PRODUTTORI': {
            }
        }
        stopwords = []
        with open(os.path.join(PRED_PATH, 'stopwords.txt'), 'r', encoding='utf-8') as f:
            text = f.readlines()
            for t in text:
                stopwords.append(t.replace('\n', ''))
            f.close()

        if not os.path.exists(os.path.join(PRED_PATH, "FULL_INFO_PRODUTTORE.csv")):
            df = pd.read_csv(os.path.join(PRED_PATH, "INFO_DB_FULL.csv"), encoding='utf-8',
                             error_bad_lines=False, skiprows=1, sep=';')
            logger.info('INDEXES = {}'.format(df.columns))
        else:
            df = pd.read_csv(os.path.join(PRED_PATH, "FULL_INFO_PRODUTTORE.csv"), encoding='utf-8',
                             error_bad_lines=False, sep=',')

        if info == 'PRODUTTORI':
            for col in ['a_prov_prod', 'a_comune_prod', 'a_via_prod', 'a_cap_prod']:
                if col == 'a_cap_prod':
                    df[col] = df[col].fillna(0)
                    df[col] = df[col].astype(int)
                    continue
                df[col] = df[col].fillna('')
                df[col] = df[col].astype(str)
                # df[col] = df[col].str.replace('', '') # RIMUOVERE LA STRINGA ' \"" ' (FATTO MANUALMENTE)
            data_prod = {
                'id_fir': df['id_fir'].to_numpy(),
                'a_rag_soc_prod': df['a_rag_soc_prod'].to_numpy(),
                'a_prov_prod': df['a_prov_prod'].to_numpy(),
                'a_comune_prod': df['a_comune_prod'].to_numpy(),
                'a_via_prod': df['a_via_prod'].to_numpy(),
                'a_cap_prod': df['a_cap_prod'].to_numpy()
            }
            df_prod = pd.DataFrame(data=data_prod)
            df_prod.to_csv(os.path.join(PRED_PATH, "FULL_INFO_PRODUTTORE.csv"))

            prod_dict = {}
            for col in ['a_rag_soc_prod', 'a_comune_prod', 'a_via_prod']:
                df_prod[col] = df_prod[col].apply(lambda cl: cl.lower())
                val_lst = df_prod[col].values
                words_prod = set()
                for txt in val_lst:
                    # logger.info(txt)
                    words_lst = [re.sub('\W', '', p) for p in word_tokenize(txt) if p not in stopwords
                                 and p not in string.punctuation and not re.search('\d', p)
                                 and not (len(p) <= 2 and re.search('\W', p))]
                    # logger.info(words_lst)
                    words_prod.update(words_lst)

                val_set = set(parola for parola in words_prod)
                foo = [el for el in val_set]
                prod_dict[col] = sorted(foo)
                # logger.info('{0} : {1} - {2}'.format(col, prod_dict[col][-30:-1], len(prod_dict[col])))
                full_info_dict[info][col] = prod_dict[col]

        return full_info_dict
    
    # def check_common_word(self, com_words, tipol):
    #     words = "'" + "','".join(com_words) + "'"
    #     q = """
    #         SELECT parola
    #         FROM COMMON_WORDS
    #         WHERE
    #         parola in ({words}) AND
    #         tipologia = '{tipologia}'
    #     """.format(words=words, tipologia=tipol)
    #     res = self.cur.execute(q).fetchall()
    #     return res
    #
    # def insert_common_words(self, info_fir=''):
    #     com_words = COMMON_FIR_INFO[self.tipologia]
    #     res = self.check_common_word(com_words, self.nome_tipologia)
    #     word_lst = []
    #     if res:
    #         for row in res:
    #             word_lst.append(row[0])
    #     word_insert = list(set(com_words) - set(word_lst))
    #     for com_word in word_insert:
    #         q = """
    #             INSERT INTO COMMON_WORDS(parola,tipologia,info,ts)
    #                     VALUES ("{com_word}","{tipologia}", "{info}", CURRENT_TIMESTAMP)
    #         """.format(com_word=com_word, tipologia=TIPO_FIR[self.tipologia]['NAME'], info=info_fir)
    #
    #         self.cur.execute(q)
    #         self.conn.commit()

    def check_ritaglio(self, delim_words, info):
        idx = []

        for txt, info_list in delim_words.items():
            for (w_id, par, cc) in info_list:
                idx.append((w_id, cc))

        r_idx = []
        id_past = (-1, -1)

        for (w_id, cc) in idx:
            # SE DOPO "PRODUTTORE" HO INFO CERCATA ALLORA MI FERMO E NON CERCO "RAGIONE" o "SOCIALE"
            if w_id > (id_past[0] + 1) and id_past[0] > 0:
                # ESCLUDO DAL RANGE (ID_INF, ID_SUP) DOVE ID_INF VIENE PRESA DA "BASSO" e ID_SUP DA "SOPRA"
                if not (id_past[1] != cc and id_past[0] > w_id):
                    self.logger.info('RANGE ID [{}, {}] PER RICERCA INFO'.format(id_past[0], w_id))
                    r_idx.append((id_past[0], w_id))

            id_past = (w_id, cc)

        rwords = []

        for ii, rx in enumerate(r_idx):
            q = """
                SELECT parola
                FROM {table} t1
                LEFT JOIN files_WEB t2
                ON (t1.id_file=t2.id)
                WHERE
                file = '{file}' AND
                t1.id BETWEEN {rx_st} AND {rx_fin};
            """.format(table='OCR_{}'.format(INFO_FIR[info.upper()]['TEXT']),
                       file=self.file_only, rx_st=rx[0] + 1, rx_fin=rx[1] - 1)

            rwords.append([item[0].lower() for item in self.cur.execute(q).fetchall()])

        self.logger.info('RANGE PAROLE TROVATE ZONA {} : {}'.format(INFO_FIR[info.upper()]['TEXT'], rwords))

        accepted_words = set()
        for k, lst in self.full_info['PRODUTTORI'].items():
            for elem in lst:
                if len(elem) >= 4 and re.search('[aeiou]$', elem):
                    accepted_words.add(elem)

        # AGGIUNGO E RIMUOVO PAROLE DA QUELLE FINORA ACCETTATE
        accepted_words = set(list(set(accepted_words) - set(INFO_FIR['PROD']['NO_WORD_OCR']))
                             + COMMON_FIR_INFO[self.tipologia])

        # TRATTENGO ALCUNE PAROLE SPECIFICHE
        for rwords_lst in rwords:
            for ii, rword in enumerate(rwords_lst):
                if rword in accepted_words:
                    rwords_lst[ii] = rword
                    continue

                if len(rword) == 1 or (rword in INFO_FIR[info.upper()]['NO_WORD_OCR']):
                    # RIMUOVO ELEMENTO MANTENDO POSIZIONE MA INSERENDO STRINGA VUOTA DA ELIMINARE ALLA FINE
                    rwords_lst[ii] = ''
                    continue

                # FAI RICERCA PAROLA LIKE DEI COMMON_FIR_INFO
                # if len(rword) > 3:
                #     common_word_like = self.word_like_cond(rword)
                #     clike = '(' + ' or '.join(common_word_like[rword]) + ')'
                #     q = """
                #         SELECT parola
                #         FROM COMMON_WORDS
                #         WHERE
                #         {clike} AND
                #         tipologia = '{tipologia}'
                #         LIMIT 1;
                #     """.format(clike=clike, tipologia=self.nome_tipologia)
                #     res = self.cur.execute(q).fetchall()
                #     if res:
                #         rword = res[0][0]
                #         rwords_lst[ii] = rword
                #         if rwords_lst[ii] in INFO_FIR[info.upper()]['NO_WORD_OCR']['{}'.format(self.tipologia)]:
                #             rwords_lst[ii] = ''
                #         continue

                rwords_lst[ii] = rword
                if (not (re.search('[aeiou]$', rword) and len(rword) > 3)) \
                        or (re.search('^[jkxyw]', rword)):
                    # RIMUOVO ELEMENTO MANTENDO POSIZIONE MA INSERENDO STRINGA VUOTA DA ELIMINARE ALLA FINE
                    rwords_lst[ii] = ''

        # CONSIDERO DIZIONARIO PAROLE ITALIANE
        d_it = enchant.Dict("it_IT")

        rws = []
        for w_l in rwords:
            if len(w_l) == 1 and w_l[0] == '':
                continue
            foo = set()
            rws.append(foo)
            for rw in w_l:
                if rw:
                    # CERCO SE PAROLA APPARTIENE ALLA LINGUA ITALIANA
                    chk = d_it.check(rw)
                    # self.logger.info('CONTROLLO APPARTENENZA {0} ALLA LINGUA ITALIANA -> {1}'.format(rw, chk))
                    if chk or rw in accepted_words:
                        foo.add(rw)

            # SE IL SET RISULTA VUOTO LO CANCELLO
            if len(foo) == 0:
                rws.remove(foo)

        # SE HO DIVERSI SET ALLORA RAGGRUPPO TUTTO IN UNO SOLO CONSIDERANDO TUTTE LE PAROLE DISTINTE
        if len(rws) > 1:
            foo = set()
            rws_agg = [foo]
            for rws_set in rws:
                for rw_el in rws_set:
                    foo.add(rw_el)

            rws = rws_agg

        # SE LA LISTA RISULTA VUOTA NON SCRIVO SUL DB
        if len(rws) == 0:
            self.logger.info('NESSUNA PAROLA INDIVIDUATA. '
                             'NESSUN INSERIMENTO IN OCR {0} PER FILE {1}.'
                             .format(INFO_FIR[info.upper()]['TEXT'], self.file_only))
            return

        # ELIMINO PAROLE OTTENUTE DA OCR AVENTI UNA LETTERA DIVERSA (ES. codine)
        # RISPETTO ALLE PAROLE NO_WORDS GIA' ELIMINATE
        btw_words = INFO_FIR[info.upper()]['BTWN_WORD']['INIZ'] + INFO_FIR[info.upper()]['BTWN_WORD']['FIN']
        ignored_words = []
        for words_lst in btw_words:
            word_like = self.word_like_cond(words_lst)

            clike = '(' + ' or '.join(word_like[words_lst]) + ')'
            subq = """
                SELECT p.parola
                FROM OCR_{table} p
                LEFT JOIN files_WEB f
                ON (p.id_file=f.id)WHERE
                file = '{file}' AND
                {clike};
            """.format(table=INFO_FIR[info.upper()]['TEXT'], file=self.file_only, clike=clike)

            # se parola like dà risultato esco subito
            res = self.cur.execute(subq).fetchall()
            if res:
                for item in self.cur.execute(subq).fetchall():
                    ignored_words.append(item[0].lower())

        for el in list(set(ignored_words)):
            if el in rws[0]:
                rws[0].remove(el)

        self.logger.info('RANGE PAROLE SELEZIONATE ZONA {} : {}'.format(INFO_FIR[info.upper()]['TEXT'], rws))
        self.logger.info('\n{0} FINE RICERCA ZONA {1} {0}\n'.format('#' * 20, INFO_FIR[info.upper()]['TEXT']))

        self.ocr_fir = {'ocr_prod': None, 'ocr_trasp': None, 'ocr_racc': None, 'ocr_{}'.format(info): rws,
                          'ocr_size': '( {} - {} )'.format(self.crop_width, self.crop_height)}

    def get_grayscale(self, image):
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def remove_noise(self, image, type='', opt=3):
        if type == 'gaussian':
            return cv2.GaussianBlur(image, (7, 7), 3)

        return cv2.medianBlur(image, opt)

    def thresholding(self, image, type='adaptive'):
        if type == 'bin+otsu':
            return cv2.threshold(image, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

        thresh = cv2.adaptiveThreshold(image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2)
        return cv2.bitwise_not(thresh)

    def rotate_file(self, img, rot=0):
        self.logger.info('FILE {0} RUOTATO DI {1} GRADI'.format(self.file_only, rot))
        img_rot = img.rotate(rot, expand=True)
        if not rot == 0:
            self.rotated_file = True
        return img_rot

    def delete_table(self, table='', info_fir=''):

        q = """
            SELECT id FROM {table}
            WHERE file = "{file}"
        """.format(table='files_WEB' if self.web else 'files', file=self.file_only)
        curr_id = self.cur.execute(q).fetchall()[0][0]

        if table == 'parole_WEB':
            q = """
                DELETE FROM "{table}"
                WHERE id_file = "{id_file}"
            """.format(table='parole_WEB' if self.web else 'parole', id_file=curr_id)

            self.logger.info('ELIMINO RECORDS PRECENDENTI NELLA TABELLA {}'
                             .format('parole_WEB' if self.web else 'parole'))
            self.cur.execute(q)
            self.conn.commit()

        elif table == 'ocr':
            q = """
                DELETE FROM "{table}"
                WHERE id_file = "{id_file}"
            """.format(table='OCR_{}'.format(info_fir), id_file=curr_id)

            self.logger.info('ELIMINO RECORDS NELLA TABELLA {}'.format('OCR_{}'.format(info_fir)))
            self.cur.execute(q)
            self.conn.commit()

    def crea_training_set(self, ocr_files, info_fir):
        """
        Metodo che ritorna una tupla di due valori:
            - l'array degli input (train_x)
            - l'array degli output (train_y)

        I due array hanno lungezza fissa:
         - len(train_x) == len(temi)
         - len(train_y) == len(info_fir)
        """
        training = []
        output_vuota = [0] * len(info_fir)
        info_fir = list(info_fir)

        for parole, file in ocr_files:
            temi_descrizione = [parola for parola in parole]

            # riempio la lista di input
            riga_input = [1 if t in temi_descrizione else 0 for t in temi]

            # riempio la lista di output
            riga_output = output_vuota[:]
            riga_output[info_fir.index(categoria)] = 1

            training.append([riga_input, riga_output])

        # mischio il mazzo
        random.shuffle(training)
        # trasformo in un array
        training = np.array(training)

        # e creo il training set
        train_x = list(training[:, 0])
        train_y = list(training[:, 1])
        return train_x, train_y


def underscore_split(file):
    if file.count("_") >= 2:
        file = "_".join(file.split("_", 2)[:2])

    if re.search('\W', file):
        file = re.sub('\W', '', file)

    return file


def process_image(curr_file):
    filepath = os.path.join(IMAGE_PATH, curr_file)
    newfilepath = os.path.join(PNG_IMAGE_PATH, curr_file)
    try:
        logger.info('CERCO FILE {0}.png IN {1}'.format(curr_file, PNG_IMAGE_PATH))
        img = Image.open(newfilepath + '.png')
    except FileNotFoundError:
        # ACCETTO DIVERSI FORMATI DI IMMAGINE DA CONVERTIRE IN PDF (PER AUMENTARE DPI)
        for ext in ['jpg', 'jpeg', 'tiff']:
            try:
                logger.info("Cerco file {0} in formato {1}".format(curr_file, ext))
                img = Image.open(filepath + '.' + ext)
            except FileNotFoundError:
                logger.info("Formato immagine {0} per file {1} non trovato".format(ext, curr_file))
                continue

            img.save(filepath + '.pdf')
            # os.remove(filepath + '.' + ext)
            break

        # CONVERTO IN PNG (NECESSARIO PER OCR)
        logger.info("Salvataggio file {0} in formato png su {1}".format(curr_file, PNG_IMAGE_PATH))
        pages = convert_from_path(filepath + '.pdf', DPI)
        for page in pages:
            page.save('{0}.png'.format(newfilepath), 'png')

        os.remove(filepath + '.pdf')
        img = Image.open(newfilepath + '.png')

    return img


# DA MODIFICARE SE VUOI USARLO
def write_info_produttori_to_csv(prod_dict):
    with open(os.path.join(PRED_PATH, 'SORTED_INFO_PROD.txt'), 'w') as f:
        f.write('PAROLE NON BANALI IN ELENCO ALFABETICO PER OGNI CAMPO DEL PRODUTTORE\n')
        for col in ['a_rag_soc_prod', 'a_comune_prod', 'a_via_prod']:
            f.write('\n{0} {1} {0}\n'.format('-' * 20, col))
            f.write('\n{}\n'.format(prod_dict[col]))
            f.write('\n{0} END {1} {0}\n'.format('-' * 20, col))

        f.close()


# def read_full_info(info=''):
#     full_info = {
#         'PRODUTTORI': {
#             'a_rag_soc_prod': [],
#             'a_comune_prod': [],
#             'a_via_prod': []
#         }
#     }
#     if info == 'PRODUTTORI':
#         with open(os.path.join(PRED_PATH, 'SORTED_INFO_PROD.txt'), 'r') as f:
#             foo = f.read()
#             f.close()
#
#         logger.info(foo)
#         quit()
#
#         for item in ['a_rag_soc_prod', 'a_comune_prod', 'a_via_prod']:
#             flag = False
#             for elem in foo:
#                 start_info = elem.startswith('{0} {1} {0}'.format('-'*20, item))
#                 end_info = elem.startswith('{0} END {1} {0}'.format('-'*20, item))
#                 if start_info or flag is True:
#                     elem = re.sub("[\[\]\n]", "", elem)
#                     if elem and not (start_info or end_info):
#                         full_info[info][item].append(elem)
#                     flag = True
#                     if end_info:
#                         break
#             logger.info(len(full_info[info][item][0]))
#
#     return full_info
    # with open (os.path.join(PRED_PATH, "FULL_INFO_PRODUTTORE.csv")) as f:
    #     for t in f.readlines():
    #         logger.info(t)
    #         break
    #
    # tb_INFO_prod = """
    #     CREATE TABLE if not exists INFO_PRODUTTORE
    #     (id INTEGER PRIMARY KEY AUTOINCREMENT,
    #     id_fir VARCHAR(1024) NOT NULL,
    #     a_rag_soc_prod VARCHAR(1024) NOT NULL,
    #     a_prov_prod VARCHAR(1024) NOT NULL,
    #     a_comune_prod VARCHAR(1024) NOT NULL,
    #     a_via_prod VARCHAR(1024) NOT NULL,
    #     a_cap_prod INTEGER NOT NULL);
    # """
    #
    # conn = sqlite3.connect(os.path.join(DB_PATH, 'OCR_MT.db'))
    # cur = conn.cursor()
    # cur.execute(tb_INFO_prod)
    #
    # for row in df_prod.itertuples():
    #     q = """
    #         INSERT INTO INFO_PRODUTTORE(id_fir, a_rag_soc_prod, a_prov_prod, a_comune_prod, a_via_prod, a_cap_prod)
    #         VALUES ("{0}","{1}","{2}","{3}","{4}","{5}")
    #     """.format(row[0], row[1], row[2], row[3], row[4], row[5])
    #     cur.execute(q)
    # conn.commit()
    #
    #
    # q = """
    #     SELECT * FROM {table}
    #     ORDER BY id_fir ASC
    # """.format(table='INFO_PRODUTTORI')


if __name__ == '__main__':
    logger.info('{0} INIZIO ESECUZIONE SCANSIONE FORMULARI RIFIUTI {0}'.format('!' * 20))
    start_time = time.time()

    # FACCIO PARTIRE I PRIMI 1000 DEI FIR CARTELLA "BULK"
    # RIMUOVI OPPURE MANTIENI ESTENSIONE FILE IN load_files_tmp!!
    load_files_tmp = ['96449_doc20210105094602172380.jpg']#os.listdir(IMAGE_PATH)[:4]#enumerate(os.listdir(IMAGE_PATH))
    load_files = []
    for elem in load_files_tmp:
        load_files.append(elem.split('.jpg')[0])
    files = []
    # full_info = read_full_info(info='PRODUTTORI')
    # # write_info_produttori_to_csv(full_info)
    # accepted_words = set()
    # foo = []
    # for k, lst in full_info['PRODUTTORI'].items():
    #     for el in lst:
    #         foo.append(el)
    #         accepted_words.add(el)
    # logger.info(len(accepted_words))
    # # logger.info(accepted_words)
    # # for el in accepted_words:
    # #     logger.info(el)
    # quit()
    # SE FILES CARICATI HANNO TANTI "_" ALLORA NE CONSIDERO SOLO UNO PER SEMPLICITA'
    for load_file in load_files:
        logger.info("VERIFICO IDENEITA' CARATTERI PER FILE {}".format(load_file))
        load_accepted_file = underscore_split(load_file)
        logger.info('NOME FILENAME ACCETTATO --> {}'.format(load_accepted_file))
        load_accepted_file = load_accepted_file + '.jpg'
        # POTREBBE ESSERE BLOCCATO IN CARTELLA IMAGES IN FORMATO PNG E QUINDI DARE ERRORE
        # LO VERIFICO POI ATTRAVERSO FUNZIONE "PROCESS_IMAGE()"
        # CONSIDERA RITAGLIO PARTE SUPERIORE PER FIR RUOTATI
        try:
            os.rename(os.path.join(IMAGE_PATH, load_file + '.jpg'), os.path.join(IMAGE_PATH, load_accepted_file))
        except FileNotFoundError:
            logger.info('FILE NON TROVATO IN {}'.format(IMAGE_PATH))
            logger.info('CERCO {0}.png in {1}'.format(load_accepted_file.split('.')[0], PNG_IMAGE_PATH))
        finally:
            files.append(load_accepted_file)

    for file in files:
        if file == '.DS_Store':
            continue
        try:
            file_only = file.split('.')[0]
            file = os.path.join(IMAGE_PATH, file)
            logger.info('\n{0} ANALISI FILE {1} {0}\n'.format('-o' * 20, file_only))
            process_image(file_only)
            file_png = os.path.join(PNG_IMAGE_PATH, file_only + '.png')
            info = GetFileInfo(file_png, logger=logger, web=True)
            ocr_fir = info.find_info()
            logger.info('\n{0} SOMMARIO FILE {1} {0}\n'.format('@' * 20, info.file_only))
            logger.info("\nFILE {0} : {1} {2}\n".format(info.file_only, info.nome_tipologia, ocr_fir))
            logger.info('\n{0} FINE SOMMARIO FILE {1} {0}\n'.format('@' * 20, info.file_only))
            if os.path.exists(os.path.join(info.file)):
                os.remove(info.file)
            # if ii == len(os.listdir(IMAGE_PATH)) - 1:
            #     info.check_ocr_files(info_ocr='prod')
        except Exception as e:
            logger.info(logging.exception('ERROR MESSAGE:'))
            with open(log_error_path, "w") as logf:
                logf.write('ERROR REPORT DATETIME {}'.format(now))
                logf.write('{0} ERROR IN FILE {1} {0}\n'.format('-' * 20, file_only))
                logf.write('{}\n'.format(str(traceback.extract_tb(e.__traceback__))))
                # logf.write('{}\n'.format(logging.exception('ERROR MESSAGE:')))
                logf.write("Exception - {0}\n".format(str(e)))
                logf.close()

    logger.info('{0} FILES PROCESSATI IN {1} SECONDI'.format(len(files), time.time() - start_time))
    logger.info('{0} ESECUZIONE SCANSIONE FORMULARI RIFIUTI TERMINATA {0}'.format('!' * 20))
