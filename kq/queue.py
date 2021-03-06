__all__ = ['Queue']

import logging
import time
import uuid

import dill
from kafka import KafkaProducer

from kq.job import Job
from kq.utils import (
    is_dict,
    is_iter,
    is_number,
    is_str,
    is_none_or_bytes,
    is_none_or_func,
    is_none_or_int,
    is_none_or_logger,
)


class Queue(object):
    """Enqueues function calls in Kafka topics as :doc:`jobs <job>`.

    :param topic: Name of the Kafka topic.
    :type topic: str
    :param producer: Kafka producer instance. For more details on producers,
        refer to kafka-python's `documentation
        <http://kafka-python.rtfd.io/en/master/#kafkaproducer>`_.
    :type producer: kafka.KafkaProducer_
    :param serializer: Callable which takes a :doc:`job <job>` namedtuple and
        returns a serialized byte string. If not set, ``dill.dumps`` is used
        by default. See :doc:`here <serializer>` for more details.
    :type serializer: callable
    :param timeout: Default job timeout threshold in seconds. If left at 0
        (default), jobs run until completion. This value can be overridden
        when enqueueing jobs.
    :type timeout: int | float
    :param logger: Logger for recording queue activities. If not set, logger
        named ``kq.queue`` is used with default settings (you need to define
        your own formatters and handlers). See :doc:`here <logging>` for more
        details.
    :type logger: logging.Logger

    **Example:**

    .. testcode::

        import requests

        from kafka import KafkaProducer
        from kq import Queue

        # Set up a Kafka producer.
        producer = KafkaProducer(bootstrap_servers='127.0.0.1:9092')

        # Set up a queue.
        queue = Queue(topic='topic', producer=producer, timeout=3600)

        # Enqueue a function call.
        job = queue.enqueue(requests.get, 'https://www.google.com/')

    .. _kafka.KafkaProducer:
        http://kafka-python.rtfd.io/en/master/apidoc/KafkaProducer.html
    """

    def __init__(self,
                 topic,
                 producer,
                 serializer=None,
                 timeout=0,
                 logger=None):

        assert is_str(topic), 'topic must be a str'
        assert isinstance(producer, KafkaProducer), 'bad producer instance'
        assert is_none_or_func(serializer), 'serializer must be a callable'
        assert is_number(timeout), 'timeout must be an int or float'
        assert timeout >= 0, 'timeout must be 0 or greater'
        assert is_none_or_logger(logger), 'bad logger instance'

        self._topic = topic
        self._hosts = producer.config['bootstrap_servers']
        self._producer = producer
        self._serializer = serializer or dill.dumps
        self._timeout = timeout
        self._logger = logger or logging.getLogger('kq.queue')
        self._default_enqueue_spec = EnqueueSpec(
            topic=self._topic,
            producer=self._producer,
            serializer=self._serializer,
            logger=self._logger,
            timeout=self._timeout,
            key=None,
            partition=None
        )

    def __repr__(self):
        """Return the string representation of the queue.

        :return: String representation of the queue.
        :rtype: str
        """
        return 'Queue(hosts={}, topic={})'.format(self._hosts, self._topic)

    def __del__(self):  # pragma: no covers
        # noinspection PyBroadException
        try:
            self._producer.close()
        except Exception:
            pass

    @property
    def hosts(self):
        """Return comma-separated Kafka hosts and ports string.

        :return: Comma-separated Kafka hosts and ports.
        :rtype: str
        """
        return self._hosts

    @property
    def topic(self):
        """Return the name of the Kafka topic.

        :return: Name of the Kafka topic.
        :rtype: str
        """
        return self._topic

    @property
    def producer(self):
        """Return the Kafka producer instance.

        :return: Kafka producer instance.
        :rtype: kafka.KafkaProducer
        """
        return self._producer

    @property
    def serializer(self):
        """Return the serializer function.

        :return: Serializer function.
        :rtype: callable
        """
        return self._serializer

    @property
    def timeout(self):
        """Return the default job timeout threshold in seconds.

        :return: Default job timeout threshold in seconds.
        :rtype: int | float
        """
        return self._timeout

    def enqueue(self, func, *args, **kwargs):
        """Enqueue a function call or a :doc:`job <job>`.

        :param func: Function or a :doc:`job <job>` object. Must be
            serializable and available to :doc:`workers <worker>`.
        :type func: callable | :doc:`kq.Job <job>`
        :param args: Positional arguments for the function. Ignored if **func**
            is a :doc:`job <job>` object.
        :param kwargs: Keyword arguments for the function. Ignored if **func**
            is a :doc:`job <job>` object.
        :return: Enqueued job.
        :rtype: :doc:`kq.Job <job>`

        **Example:**

        .. testcode::

            import requests

            from kafka import KafkaProducer
            from kq import  Job, Queue

            # Set up a Kafka producer.
            producer = KafkaProducer(bootstrap_servers='127.0.0.1:9092')

            # Set up a queue.
            queue = Queue(topic='topic', producer=producer)

            # Enqueue a function call.
            queue.enqueue(requests.get, 'https://www.google.com/')

            # Enqueue a job object.
            job = Job(func=requests.get, args=['https://www.google.com/'])
            queue.enqueue(job)

        .. note::

            The following rules apply when enqueueing a :doc:`job <job>`:

            * If ``Job.id`` is not set, a random one is generated.
            * If ``Job.timestamp`` is set, it is replaced with current time.
            * If ``Job.topic`` is set, it is replaced with current topic.
            * If ``Job.timeout`` is set, its value overrides others.
            * If ``Job.key`` is set, its value overrides others.
            * If ``Job.partition`` is set, its value overrides others.

        """
        return self._default_enqueue_spec.enqueue(func, *args, **kwargs)

    def using(self, timeout=None, key=None, partition=None):
        """Set enqueue specifications such as timeout, key and partition.

        :param timeout: Job timeout threshold in seconds. If not set, default
            timeout (specified during queue initialization) is used instead.
        :type timeout: int | float
        :param key: Kafka message key. Jobs with the same keys are sent to the
            same topic partition and executed sequentially. Applies only if the
            **partition** parameter is not set, and the producer’s partitioner
            configuration is left as default. For more details on producers,
            refer to kafka-python's documentation_.
        :type key: bytes
        :param partition: Topic partition the message is sent to. If not set,
            the producer's partitioner selects the partition. For more details
            on producers, refer to kafka-python's documentation_.
        :type partition: int
        :return: Enqueue specification object which has an ``enqueue`` method
            with the same signature as :func:`kq.queue.Queue.enqueue`.

        **Example:**

        .. testcode::

            import requests

            from kafka import KafkaProducer
            from kq import Job, Queue

            # Set up a Kafka producer.
            producer = KafkaProducer(bootstrap_servers='127.0.0.1:9092')

            # Set up a queue.
            queue = Queue(topic='topic', producer=producer)

            url = 'https://www.google.com/'

            # Enqueue a function call in partition 0 with message key 'foo'.
            queue.using(partition=0, key=b'foo').enqueue(requests.get, url)

            # Enqueue a function call with a timeout of 10 seconds.
            queue.using(timeout=10).enqueue(requests.get, url)

            # Job values are preferred over values set with "using" method.
            job = Job(func=requests.get, args=[url], timeout=5)
            queue.using(timeout=10).enqueue(job)  # timeout is still 5

        .. _documentation: http://kafka-python.rtfd.io/en/master/#kafkaproducer
        """
        return EnqueueSpec(
            topic=self._topic,
            producer=self._producer,
            serializer=self._serializer,
            logger=self._logger,
            timeout=timeout or self._timeout,
            key=key,
            partition=partition
        )


class EnqueueSpec(object):

    __slots__ = [
        '_topic',
        '_producer',
        '_serializer',
        '_logger',
        '_timeout',
        '_key',
        '_part',
        'delay'
    ]

    def __init__(self,
                 topic,
                 producer,
                 serializer,
                 logger,
                 timeout,
                 key,
                 partition):
        assert is_number(timeout), 'timeout must be an int or float'
        assert is_none_or_bytes(key), 'key must be a bytes'
        assert is_none_or_int(partition), 'partition must be an int'

        self._topic = topic
        self._producer = producer
        self._serializer = serializer
        self._logger = logger
        self._timeout = timeout
        self._key = key
        self._part = partition

    def enqueue(self, obj, *args, **kwargs):
        """Enqueue a function call or :doc:`job` instance.

        :param func: Function or :doc:`job <job>`. Must be serializable and
            importable by :doc:`worker <worker>` processes.
        :type func: callable | :doc:`kq.Job <job>`
        :param args: Positional arguments for the function. Ignored if **func**
            is a :doc:`job <job>` object.
        :param kwargs: Keyword arguments for the function. Ignored if **func**
            is a :doc:`job <job>` object.
        :return: Enqueued job.
        :rtype: :doc:`kq.Job <job>`
        """
        timestamp = int(time.time() * 1000)

        if isinstance(obj, Job):
            job_id = uuid.uuid4().hex if obj.id is None else obj.id
            func = obj.func
            args = tuple() if obj.args is None else obj.args
            kwargs = {} if obj.kwargs is None else obj.kwargs
            timeout = self._timeout if obj.timeout is None else obj.timeout
            key = self._key if obj.key is None else obj.key
            partition = self._part if obj.partition is None else obj.partition

            assert is_str(job_id), 'Job.id must be a str'
            assert callable(func), 'Job.func must be a callable'
            assert is_iter(args), 'Job.args must be a list or tuple'
            assert is_dict(kwargs), 'Job.kwargs must be a dict'
            assert is_number(timeout), 'Job.timeout must be an int or float'
            assert is_none_or_bytes(key), 'Job.key must be a bytes'
            assert is_none_or_int(partition), 'Job.partition must be an int'
        else:
            assert callable(obj), 'first argument must be a callable'
            job_id = uuid.uuid4().hex
            func = obj
            args = args
            kwargs = kwargs
            timeout = self._timeout
            key = self._key
            partition = self._part

        job = Job(
            id=job_id,
            timestamp=timestamp,
            topic=self._topic,
            func=func,
            args=args,
            kwargs=kwargs,
            timeout=timeout,
            key=key,
            partition=partition
        )
        self._logger.info('Enqueueing {} ...'.format(job))
        self._producer.send(
            self._topic,
            value=self._serializer(job),
            key=self._serializer(key) if key else None,
            partition=partition,
            timestamp_ms=timestamp
        )
        return job
