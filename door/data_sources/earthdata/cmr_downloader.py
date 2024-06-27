from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor
from urllib.error import URLError, HTTPError
import ssl

import json
import itertools
import sys
import numpy as np
from datetime import datetime, timedelta

from osgeo import gdal

from ...base_downloaders import DOORDownloader
from ...utils.auth import get_credentials
from ...utils.space import BoundingBox

class CMRDownloader(DOORDownloader):
    """
    This class is a downloader for through the Common Metadata Repository (CMR).
    This includes NASA's Earthdata such as VIIRS and MODIS data.

    In this common class, we will implement the methods used to navigate the CMR and download raw data.
    """

    name = "CMR_downloader"

    urs_url='https://urs.earthdata.nasa.gov'
    cmr_url='https://cmr.earthdata.nasa.gov/search/granules.json?'

    credential_env_vars = {'username' : 'EARTHDATA_LOGIN', 'password' : 'EARTHDATA_PWD'}

    cmr_page_size = 200 #TODO: check if true

    default_options = {
        'layers': None,
        'make_mosaic': True,
        'crop_to_bounds': True,
        'keep_tiles_naming': False,
    }

    file_ext = ['.hdf', '.h5']

    def __init__(self) -> None:
        super().__init__()
    
    @property
    def variable(self):
        return self._variable
    
    @variable.setter
    def variable(self, variable: str):
        available_list = self.get_available_variables().keys()
        # check if the variable is available
        if variable.lower() not in available_list:
            msg = f'Variable {variable} is not available. Available variables are: '
            msg += ', '.join(available_list)
            raise ValueError(msg)
        
        # set the variable
        self._variable = variable.lower()

        # add the variable-specific parameters
        varopts = self.get_available_variables()[self._variable]
        self.provider  = varopts['provider']
        self.product   = varopts['product']
        self.version   = varopts['version']
        self.timesteps = varopts['timesteps']
        self.layers = varopts['layers']

    def get_credentials(self, url: str) -> str:

        # credentials will be looked for in the environment variables
        # username = 'EARTHDATA_LOGIN', password = 'EARTHDATA_PWD'
        # should be saved in a .netrc file in the user's home directory
        # with the following line:
        # machine urs.earthdata.nasa.gov login <username> password <password>
        if not hasattr(self, 'credentials') or not isinstance(self.credentials, str):
            self.credentials = get_credentials(env_variables=self.credential_env_vars,
                                               url=self.urs_url, test_url=url)
        
        return self.credentials

    def get_last_published_date(self) -> datetime:
        """
        Get the last published date for the dataset.
        """
        
        global_bounds = BoundingBox(-180, -90, 180, 90)
        now = datetime.now()
        time = datetime(now.year, now.month, now.day)

        while True:
            urls = self.cmr_search(time, global_bounds)
            if urls:
                break
            time -= timedelta(days=1)
        
        # time is now the end of the timestep that the data refers to
        # we need to find the beginning, so we keep going back until the list of urls changes
        new_urls = urls.copy()
        while all([url in new_urls for url in urls]):
            time -= timedelta(days=1)
            new_urls = self.cmr_search(time, global_bounds)
        
        return time + timedelta(days=1)

    def download(self, url_list: list[str], destination: str, trials = 100) -> list[str]:
        """
        Downloads the files from the urls in the list
        """
        if not url_list:
            return

        filename_ls = []

        max_log_step = 20
        log_step_percentage = 10
        log_step = min(max_log_step, np.ceil(len(url_list)/log_step_percentage))

        for n, url in enumerate(url_list):
            credentials = self.get_credentials(url)
            filename = url.split('/')[-1]

            trial = 1
            while trial <= trials:
                try:
                    req = Request(url)
                    if credentials:
                        req.add_header('Authorization', 'Basic {0}'.format(credentials))
                    opener = build_opener(HTTPCookieProcessor())
                    data = opener.open(req).read()

                    filename_save = destination + '/' + filename
                    with open(filename_save, 'wb') as f:
                        f.write(data)
                    
                    #open(filename_save, 'wb').write(data)
                    
                    if filename_save.find('.xml') > 0:
                        continue
                    if filename_save.find('s3credentials') < 0:
                        filename_ls.append(filename_save)

                    if n % log_step == 0 or n == len(url_list)-1:
                        self.log.info(f'  -> Downloaded {n+1} of {len(url_list)} files')
                    
                    break

                except HTTPError as e:
                    self.log.info(f'HTTP error {e.code}, {e.reason} - trying again {trial}/{trials}')
                    trial += 1
                except URLError as e:
                    self.log.error('URL error {0}'.format(e.reason))
                    raise
                except OSError as e:
                    self.log.error('IO error {0}'.format(e))
                    raise
                except KeyboardInterrupt:
                    quit()

        with open(destination + '/hdf5names.json', 'w') as f:
            json.dump(filename_ls, f) 

        return filename_ls       

    def build_cmr_query(self, time_start: datetime, time_end: datetime, bounding_box) -> str:

        cmr_base_url = ('{0}provider={1}'
                        '&sort_key=start_date&sort_key=producer_granule_id'
                        '&scroll=true&page_size={2}'.format(self.cmr_url, self.provider, self.cmr_page_size))

        product_query = self.fomat_product(self.product)
        version_query = self.format_version(self.version)
        temporal_query = self.format_temporal(time_start, time_end)
        spatial_query = self.format_spatial(bounding_box)
        #filter_query = self.format_filename_filter(time)

        tail = '&options[producer_granule_id][pattern]=true'

        return cmr_base_url + product_query + version_query + temporal_query + spatial_query + tail# + filter_query

    def cmr_search(self, time: datetime|tuple[datetime], space_bounds: BoundingBox) -> dict:
        """
        Search CMR for files matching the query.
        """

        bounding_box = space_bounds.bbox

        if isinstance(time, tuple):
            time_start, time_end = time
        else:
            time_start = time
            time_end = time

        cmr_query_url = self.build_cmr_query(time_start, time_end, bounding_box)
        cmr_scroll_id = None
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            urls = []
            while True:
                req = Request(cmr_query_url)
                if cmr_scroll_id:
                    req.add_header('cmr-scroll-id', cmr_scroll_id)
                response = urlopen(req, context=ctx)
                if not cmr_scroll_id:
                    # Python 2 and 3 have different case for the http headers
                    headers = {k.lower(): v for k, v in dict(response.info()).items()}
                    cmr_scroll_id = headers['cmr-scroll-id']
                    hits = int(headers['cmr-hits'])
                search_page = response.read()
                search_page = json.loads(search_page.decode('utf-8'))
                url_scroll_results = cmr_filter_urls(search_page, extensions=self.file_ext)
                if not url_scroll_results:
                    break
                if hits > self.cmr_page_size:
                    sys.stdout.flush()
                urls += url_scroll_results

            if hits > self.cmr_page_size:
                print()
            return urls
        except KeyboardInterrupt:
            quit()

    @staticmethod
    def fomat_product(product: str) -> str:
        """
        Formats the product name to be used in the CMR query.
        """
        return f'&short_name={product}'
    
    @staticmethod
    def format_version(version: str) -> str:
        """
        Formats the version to be used in the CMR query.
        """
        desired_pad_length = 3
        try:
            version = str(int(version))  # Strip off any leading zeros
            query_params = ''
            while len(version) <= desired_pad_length:
                padded_version = version.zfill(desired_pad_length)
                query_params += f'&version={padded_version}'
                desired_pad_length -= 1
        except ValueError:
            query_params = f'&version={version}'

        return query_params

    @staticmethod
    def format_temporal(time_start: datetime, time_end:datetime) -> str:
        """
        Formats the time to be used in the CMR query.
        """
        date_start_st = time_start.strftime('%Y-%m-%d')
        time_start_st = date_start_st + 'T00:00:00Z'

        date_end_st = time_end.strftime('%Y-%m-%d')
        time_end_st = date_end_st + 'T23:59:59Z'
        return f'&temporal={time_start_st},{time_end_st}'
    
    @staticmethod
    def format_spatial(bounding_box) -> str:
        """
        Formats the spatial extent to be used in the CMR query.
        """
        bbformat = '{0},{1},{2},{3}'.format(bounding_box[0], bounding_box[1], bounding_box[2], bounding_box[3])
        return f'&bounding_box={bbformat}'
    
    @staticmethod
    def format_filename_filter(time: datetime) -> str:
        """
        Formats the filename filter to be used in the CMR query.
        """
        filename_filter = time.strftime('*A%Y%j*')
        return f'&producer_granule_id[]={filename_filter}&options[producer_granule_id][pattern]=true'
    
    @classmethod
    def get_available_variables(cls) -> dict:
        available_variables = cls.available_variables
        return {v: {'provider' : available_variables[v][0],\
                    'product'  : available_variables[v][1],
                    'version'  : available_variables[v][2],
                    'timesteps': available_variables[v][3],
                    'layers'   : [{'id'   : l[0],
                                   'name' : l[1],
                                   #'range': l[2],
                                   #'scale': l[3],
                                   #'type' : l[4]
                                   } for l in available_variables[v][4]]}\
                for v in available_variables}
    
def cmr_filter_urls(search_results, extensions=['.hdf', '.h5']):
    """Select only the desired data files from CMR response."""
    if 'feed' not in search_results or 'entry' not in search_results['feed']:
        return []

    entries = [e['links']
               for e in search_results['feed']['entry']
               if 'links' in e]
    # Flatten "entries" to a simple list of links
    links = list(itertools.chain(*entries))

    urls = []
    unique_filenames = set()
    for link in links:
        if 'href' not in link:
            # Exclude links with nothing to download
            continue
        if 'inherited' in link and link['inherited'] is True:
            # Why are we excluding these links?
            continue
        if 'rel' in link and 'data#' not in link['rel']:
            # Exclude links which are not classified by CMR as "data" or "metadata"
            continue

        if 'title' in link and 'opendap' in link['title'].lower():
            # Exclude OPeNDAP links--they are responsible for many duplicates
            # This is a hack; when the metadata is updated to properly identify
            # non-datapool links, we should be able to do this in a non-hack way
            continue

        extensions = [ext.lower().replace('.','') for ext in extensions]
        if link['href'].split('.')[-1].lower() not in extensions:
            # Exclude links with non-desired extensions
            continue

        filename = link['href'].split('/')[-1]
        if filename in unique_filenames:
            # Exclude links with duplicate filenames (they would overwrite)
            continue
        unique_filenames.add(filename)

        urls.append(link['href'])

    return urls