# form_processor.py
import re

class FormProcessor:
    def __init__(self, form_data, route):
        self.route = route
        if self.route == 'bach':
            self.u = form_data.get('u', '')
            self.Uu = form_data.get('Uu', '')
            self.estado = form_data.get('estado', '')
        else:
            self.u = form_data.get('u', '')
            self.area = form_data.get('area', '')
            self.carrera = form_data.get('carrera', '')

        self.asignatura = form_data.get('asignatura', '')
        self.periodo = form_data.get('periodo', '')
        self.academico = form_data.get('academico', '')
        self.city = form_data.get('city', '')
        self.date = form_data.get('date', '')
        self.teacher = form_data.get('teacher', '')
        self.title = form_data.get('title', 'untitled')
        self.seccion = form_data.get('seccion', '')
        self.input1 = form_data.get('input1', '')
        self.input2 = form_data.get('input2', '')
        self.input3 = form_data.get('input3', '')
        self.input4 = form_data.get('input4', '')
        self.input5 = form_data.get('input5', '')
        self.input6 = form_data.get('input6', '')
        self.input7 = form_data.get('input7', '')
        self.input8 = form_data.get('input8', '')
        self.id1 = form_data.get('id1', '')
        self.id2 = form_data.get('id2', '')
        self.id3 = form_data.get('id3', '')
        self.id4 = form_data.get('id4', '')
        self.id5 = form_data.get('id5', '')
        self.id6 = form_data.get('id6', '')
        self.id7 = form_data.get('id7', '')
        self.id8 = form_data.get('id8', '')
        self.body = form_data.get('body', '')
        self.introduccion = form_data.get('introduccion', '')
        self.conclusion = form_data.get('conclusion', '')
        self.select = form_data.get('gblock-template-canvas-integrantes', '')
        self.subtitle_1 = form_data.get('subtitle_1', '')
        self.subtitle_2 = form_data.get('subtitle_2', '')
        self.subtitle_3 = form_data.get('subtitle_3', '')
        self.subtitle_4 = form_data.get('subtitle_4', '')
        self.subtitle_5 = form_data.get('subtitle_5', '')
        self.subtitle_6 = form_data.get('subtitle_6', '')
        self.subtitle_7 = form_data.get('subtitle_7', '')
        self.subtitle_8 = form_data.get('subtitle_8', '')


            # Llamar al método para crear el nuevo título con subtítulos
        self.subtitles = self.create_subtitles()
        self.title_corrected = False

                # Llamar al método para crear el nuevo título con subtítulos
        self.formatted_title = self.create_title_with_subtitles()

    def apply_formatted_texts(self, formatted):
        """Aplica el resultado de text_format.format_texts: título y subtítulos con sus
        mayúsculas y tildes. Debe llamarse antes de process()."""
        self.original_title = self.title
        self.title = formatted['title']
        self.title_corrected = formatted['title_corrected']
        names = [f'subtitle_{i}' for i in range(1, 9) if getattr(self, f'subtitle_{i}')]
        names = [name for name in names if self.clean_subtitle(getattr(self, name))]
        for name, text in zip(names, formatted['subtitles']):
            setattr(self, name, text)
        self.subtitles = self.create_subtitles()
        self.formatted_title = self.create_title_with_subtitles()

    def create_title_with_subtitles(self):
        # Crear una lista con los subtítulos que no estén vacíos
        subtitles = [self.subtitle_1, self.subtitle_2, self.subtitle_3, self.subtitle_4,
                     self.subtitle_5, self.subtitle_6, self.subtitle_7, self.subtitle_8]

        # Filtrar los subtítulos no vacíos
        valid_subtitles = [subtitle for subtitle in subtitles if subtitle]

        # Si hay subtítulos válidos, añadirlos entre paréntesis al título, de lo contrario solo devolver el título
        if valid_subtitles:
            return f"{self.title} ({', '.join(valid_subtitles)})"
        else:
            return self.title

    def create_subtitles(self):
        """Devuelve la lista de subtítulos no vacíos, en orden.

        El armado del texto que se le manda a Gemini (con sus comillas y
        conectores) es responsabilidad de generate_essay_content en IA.py,
        no de este método: aquí sólo se filtra y se entrega la lista.
        """
        subtitles = [self.subtitle_1, self.subtitle_2, self.subtitle_3, self.subtitle_4,
                     self.subtitle_5, self.subtitle_6, self.subtitle_7, self.subtitle_8]
        cleaned = [self.clean_subtitle(subtitle) for subtitle in subtitles if subtitle]
        return [subtitle for subtitle in cleaned if subtitle]

    @staticmethod
    def clean_subtitle(subtitle):
        """Subtítulo listo para usar: sin puntos suspensivos ni ':' al final y con mayúscula inicial.

        Se limpia antes de mandarlo a la IA: si se le pasa 'que es...' tal cual, lo
        copia con los puntos y en minúscula y el documento lo trata como un párrafo."""
        text = re.sub(r'\s+', ' ', str(subtitle)).strip().rstrip('.…:;, ').strip()
        return text[:1].upper() + text[1:]

    def process(self):
        

        self.body = f"          {self.body}" if self.body else ''
        self.introduccion = f"          {self.introduccion}" if self.introduccion else ''
        self.conclusion = f"          {self.conclusion}" if self.conclusion else ''
        
        self.process_select()
        self.process_fields()
        
    def process_select(self):
        if self.select == '':
            self.clear_inputs_and_ids()
        else:
            self.clear_from_select(int(self.select))

    def clear_inputs_and_ids(self):
        for i in range(1, 9):
            setattr(self, f'input{i}', '')
            setattr(self, f'id{i}', '')

    def clear_from_select(self, select):
        for i in range(select + 1, 9):
            setattr(self, f'input{i}', '')
            setattr(self, f'id{i}', '')

    def process_fields(self):

        if self.route == 'bach':
            self.Uu = f"{self.Uu} " if self.Uu else ''
            self.u = f"\"{self.u}\"" if self.u else ''
            
            if self.city and self.estado:
                self.city = f"{self.city} - Edo. "
            
            if not self.Uu and not self.u:
                self.Uu = self.city
                self.u = self.estado
                self.city = ''
                self.estado = ''

            self.academico = f"{self.academico} " if self.academico else ''
            self.periodo = f"{self.periodo} " if self.periodo else ''
            self.academico = f"{self.academico}" if self.periodo else self.academico
            self.seccion = f'SECCION: \"{self.seccion}\"' if self.seccion else ''
            self.docente = 'DOCENTE:' if self.teacher else ''
            self.asignatura_t = 'MATERIA:' if self.asignatura else ''

        else:
            if self.city and self.date:
                self.city = f"{self.city}, "
            
            if self.academico:
                self.academico = f"{self.academico}: "
            
            self.academico2 = ''
            self.periodo2 = ''
            self.asignatura2 = ''
            self.asignatura_t = ''
            if self.asignatura:
                self.asignatura_t = 'MATERIA: '
                if len(self.asignatura) >= 17:
                    self.asignatura2 = self.asignatura
                    self.asignatura = ''
                    self.academico2 = self.academico
                    self.periodo2 = self.periodo
                    self.periodo = ''
                    self.academico = ''
                    
            if not self.u:
                self.u = self.area
                self.area = ''
            
            if not self.area:
                self.area = self.carrera
                self.carrera = ''

            self.seccion = f'SECCION: \"{self.seccion}\"' if self.seccion else ''
            self.docente = 'DOCENTE:' if self.teacher else ''        
        self.alumnos = 'ALUMNOS:' if any(getattr(self, f'input{i}') for i in range(2, 9)) else ('ALUMNO:' if self.input1 else '')

        for i in range(1, 9):
            if getattr(self, f'id{i}'):
                setattr(self, f'id{i}', f" C.I- {getattr(self, f'id{i}')}")
                
    def __getitem__(self, item):
        return getattr(self, item)

    @staticmethod
    def capitalizar_frases(cadena):
        palabras = cadena.split()
        palabras_capitalizadas = []
        for palabra in palabras:
            if palabra.lower() in ['de', 'del', 'la', 'las', 'los', 'y', 'para', 'sus', 'su', 'al'] and palabras_capitalizadas != []:
                palabras_capitalizadas.append(palabra.lower())
            else:
                palabras_capitalizadas.append(palabra.capitalize())
        nueva_cadena = ' '.join(palabras_capitalizadas)
        return nueva_cadena
    
    @staticmethod
    def fecha(date):
        string = ''
        if date != '':
            day = date[0:2]
            month = date[3:5]
            year = date[6:10]
            months = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio', 'Julio',
                    'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']
            new_month = months[int(month) - 1]
            string = f'{day} de {new_month} de {year}'

        return string

    def generate_replacements(self):
        # Con el título ya corregido por text_format se respetan siglas y nombres propios
        # (capitalizar_frases pasaría 'UCV' a 'Ucv'); si no, el formato de siempre.
        head_title = self.title if getattr(self, 'title_corrected', False) else self.capitalizar_frases(self.title)
        new_date = self.fecha(self.date)

        if self.route == 'bach':
            cadenas = [self.input1, self.input2, self.input3, self.input4, self.input5, self.input6, self.input7, self.input8,
                   self.estado, self.asignatura, self.periodo, self.academico,
                   self.city, self.seccion, self.teacher, self.title, self.u, new_date]
        else:
            cadenas = [self.input1, self.input2, self.input3, self.input4, self.input5, self.input6, self.input7, self.input8,
                    self.area, self.carrera, self.asignatura, self.periodo, self.academico,
                    self.city, self.seccion, self.teacher, self.title, self.u, self.asignatura2, new_date, self.academico2, self.periodo2]

        cadenas_en_mayusculas = [cadena.upper() for cadena in cadenas]
        cadenas_sin_doble_espacio = [cadena.replace('  ', ' ') for cadena in cadenas_en_mayusculas]

        diccionario_cadenas = {}
        for i, cadena in enumerate(cadenas_sin_doble_espacio):
            clave = f'cadena{i+1}'
            diccionario_cadenas[clave] = cadena


        if self.route == 'bach':
            self.input1 = diccionario_cadenas['cadena1']
            self.input2 = diccionario_cadenas['cadena2']
            self.input3 = diccionario_cadenas['cadena3']
            self.input4 = diccionario_cadenas['cadena4']
            self.input5 = diccionario_cadenas['cadena5']
            self.input6 = diccionario_cadenas['cadena6']
            self.input7 = diccionario_cadenas['cadena7']
            self.input8 = diccionario_cadenas['cadena8']
            self.estado = diccionario_cadenas['cadena9']
            self.asignatura = diccionario_cadenas['cadena10']
            self.periodo = diccionario_cadenas['cadena11']
            self.academico = diccionario_cadenas['cadena12']
            self.city = diccionario_cadenas['cadena13']
            self.seccion = diccionario_cadenas['cadena14']
            self.teacher = diccionario_cadenas['cadena15']
            self.title = diccionario_cadenas['cadena16']
            self.u = diccionario_cadenas['cadena17']
            self.date = diccionario_cadenas['cadena18']

            return {
            '[u]': self.u,
            '[Uu]': self.Uu,
            '[city]': self.city,
            '[date]': self.date,
            '[seccion]': self.seccion,
            '[title]': self.title,
            '[docente]': self.docente,
            '[teacher]': self.teacher,
            '[estado]': self.estado,
            '[asignatura]': self.asignatura,
            '[asignatura_t]': self.asignatura_t,
            '[periodo]': self.periodo,
            '[academico]': self.academico,
            '[alumnos]': self.alumnos,
            '[1]': self.input1,
            '[2]': self.input2,
            '[3]': self.input3,
            '[4]': self.input4,
            '[5]': self.input5,
            '[6]': self.input6,
            '[7]': self.input7,
            '[8]': self.input8,
            '[id1]': self.id1,
            '[id2]': self.id2,
            '[id3]': self.id3,
            '[id4]': self.id4,
            '[id5]': self.id5,
            '[id6]': self.id6,
            '[id7]': self.id7,
            '[id8]': self.id8
        }, head_title


        self.input1 = diccionario_cadenas['cadena1']  # Salida: CADENA1
        self.input2 = diccionario_cadenas['cadena2']  # Salida: CADENA2
        self.input3 = diccionario_cadenas['cadena3']  # Salida: CADENA3
        self.input4 = diccionario_cadenas['cadena4']  # Salida: CADENA4
        self.input5 = diccionario_cadenas['cadena5']  # Salida: CADENA5
        self.input6 = diccionario_cadenas['cadena6']  # Salida: CADENA6
        self.input7 = diccionario_cadenas['cadena7']  # Salida: CADENA5
        self.input8 = diccionario_cadenas['cadena8']  # Salida: CADENA6
        self.area = diccionario_cadenas['cadena9']  # Salida: CADENA7
        self.carrera = diccionario_cadenas['cadena10']    # Salida: CADENA8
        self.asignatura = diccionario_cadenas['cadena11'] # Salida: CADENA9
        self.periodo = diccionario_cadenas['cadena12']   # Salida: CADENA10
        self.academico = diccionario_cadenas['cadena13']   # Salida: CADENA11
        self.city = diccionario_cadenas['cadena14']   # Salida: CADENA12
        self.seccion = diccionario_cadenas['cadena15']  # Salida: CADENA13
        self.teacher = diccionario_cadenas['cadena16']  # Salida: CADENA14
        self.title = diccionario_cadenas['cadena17']  # Salida: CADENA15
        self.u = diccionario_cadenas['cadena18'] # Salida: CADENA16
        self.asignatura2 = diccionario_cadenas['cadena19']
        self.date = diccionario_cadenas['cadena20']
        self.academico2 = diccionario_cadenas['cadena21']
        self.periodo2 = diccionario_cadenas['cadena22']

        return {
        '[u]': self.u,
        '[area]': self.area,
        '[city]': self.city,
        '[date]': self.date,
        '[seccion]': self.seccion,
        '[title]' : self.title,
        '[docente]' : self.docente,
        '[teacher]': self.teacher,
        '[carrera]': self.carrera,
        '[asignatura]': self.asignatura,
        '[asignatura2]':self.asignatura2,
        '[asignatura_t]':self.asignatura_t,
        '[periodo]': self.periodo,
        '[academico]':self.academico,
        '[academico2]':self.academico2,
        '[periodo2]':self.periodo2,
        '[alumnos]':self.alumnos,
        '[1]': self.input1,
        '[2]': self.input2,
        '[3]': self.input3,
        '[4]': self.input4,
        '[5]': self.input5,
        '[6]': self.input6,
        '[7]': self.input7,
        '[8]': self.input8,
        '[id1]': self.id1,
        '[id2]': self.id2,
        '[id3]': self.id3,
        '[id4]': self.id4,
        '[id5]': self.id5,
        '[id6]': self.id6,
        '[id7]': self.id7,
        '[id8]': self.id8
        }, head_title